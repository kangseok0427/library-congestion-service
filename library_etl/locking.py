"""OS-owned advisory lock shared by CLI and server processes on local disk."""
import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path

from backend.domain import DataError


def storage_directory(path):
    path = Path(path).resolve()
    return path.with_name(path.name + '.versions')


@contextmanager
def data_lock(path, *, wait=False):
    directory = storage_directory(path)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'writer.lock').open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + (10 if wait else 0)
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise DataError('데이터 변경 잠금이 사용 중입니다.', 'PUBLISH_IN_PROGRESS') from exc
                time.sleep(.02)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
