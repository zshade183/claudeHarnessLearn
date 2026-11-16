"""任务和工作流共用的跨进程文件锁。"""

import errno
import os
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def file_lock(path: Path, *, blocking: bool = True):
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(handle.fileno(), mode, 1)
            else:
                mode = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(handle.fileno(), mode)
        except OSError as error:
            if not blocking and error.errno in {errno.EACCES, errno.EAGAIN}:
                raise BlockingIOError(f"Task is in use: {path.stem}") from error
            raise

        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
