"""Atomic journals, content checks and crash-released local project locks."""
import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".journal-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass  # Some external filesystems do not implement directory fsync.
    finally:
        Path(temp).unlink(missing_ok=True)


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def file_record(path, cancel=lambda: False):
    sha = hashlib.sha256()
    with open(path, "rb") as source:
        while True:
            if cancel():
                from engine import Cancelled
                raise Cancelled("Abgebrochen")
            chunk = source.read(4 * 1024 * 1024)
            if not chunk:
                break
            sha.update(chunk)
    return {"size": Path(path).stat().st_size, "sha256": sha.hexdigest()}


def valid_file(path, record=None, full=False, cancel=lambda: False):
    if not path or not Path(path).is_file():
        return False
    if not record:
        return True  # Legacy V7 clips are decoded before use by a worker.
    if Path(path).stat().st_size != record.get("size"):
        return False
    return not full or file_record(path, cancel) == record


@contextlib.contextmanager
def file_lock(path):
    import fcntl  # macOS and Linux; locks are released even after a process crash.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Dieses Projekt wird bereits in einem anderen Fenster berechnet.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
