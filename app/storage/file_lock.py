"""Cross-platform thread/process file locking and atomic write utilities."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Iterator


class StorageError(Exception):
    """Raised when local storage operations fail safely."""


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


def _get_thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        if key not in _THREAD_LOCKS:
            _THREAD_LOCKS[key] = threading.RLock()
        return _THREAD_LOCKS[key]


@contextmanager
def file_lock(
    target_path: Path,
    timeout_seconds: float = 10.0,
    stale_lock_seconds: float = 60.0,
) -> Iterator[None]:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target_path.with_name(f"{target_path.name}.lock")
    thread_lock = _get_thread_lock(target_path)

    with thread_lock:
        deadline = time.monotonic() + timeout_seconds
        fd: int | None = None

        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, f"{os.getpid()}".encode("ascii"))
                break
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > stale_lock_seconds:
                        lock_path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise StorageError(f"Timed out waiting for lock on '{target_path}'.")
                time.sleep(0.02)
            except OSError as exc:
                raise StorageError(f"Unable to acquire lock for '{target_path}': {exc}") from exc

        try:
            yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_text(target_path: Path, content: str, encoding: str = "utf-8") -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=str(target_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        for attempt in range(5):
            try:
                os.replace(str(tmp_path), str(target_path))
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise StorageError(f"Failed atomic write to '{target_path}': {exc}") from exc