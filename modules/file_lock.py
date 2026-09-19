"""Small cross-platform blocking file lock without an extra dependency."""

import os
import time
from pathlib import Path


class FileLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._file = None

    def _ensure_initialized(self) -> None:
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            try:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                if self.path.stat().st_size >= 1:
                    return
            except FileNotFoundError:
                return self._ensure_initialized()
            time.sleep(0.01)

        # Recover a zero-byte file left by a process that crashed during creation.
        with self.path.open("r+b") as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"0")
                stream.flush()
                os.fsync(stream.fileno())

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_initialized()
        self._file = self.path.open("r+b")
        self._file.seek(0)

        if os.name == "nt":
            import msvcrt

            while True:
                self._file.seek(0)
                try:
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.winerror not in (33, 36) and exc.errno not in (11, 13):
                        self._file.close()
                        self._file = None
                        raise
                    time.sleep(0.1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._file is None:
            return
        if os.name == "nt":
            import msvcrt

            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None