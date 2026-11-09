import os
import sys


def fsync_fd(fd: int):
    """Durable sync: F_FULLFSYNC on macOS, standard fsync elsewhere."""
    if sys.platform == 'darwin':
        F_FULLFSYNC = 51
        try:
            import fcntl
            fcntl.fcntl(fd, F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(fd)


def fsync_file(path: str):
    fd = os.open(path, os.O_RDONLY)
    try:
        fsync_fd(fd)
    finally:
        os.close(fd)


def fsync_dir(path: str):
    """fsync directory so that a preceding rename() becomes durable."""
    fd = os.open(path, os.O_RDONLY)
    try:
        fsync_fd(fd)
    finally:
        os.close(fd)
