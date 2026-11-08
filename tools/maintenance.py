"""Cross-process admission barrier used only when explicitly configured."""
from contextlib import contextmanager
import fcntl
import os


@contextmanager
def lease():
    path = os.environ.get('PLOTLINE_ADMISSION_LOCK')
    if not path:
        yield
        return
    with open(path, 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield
