"""Child processes that end when Tamra ends, even when Tamra crashes or is killed.

Windows: a kill-on-close Job Object (tamra.winjob). Tamra holds the only handle, so Windows ends
the job's processes when Tamra exits.
Linux: PR_SET_PDEATHSIG. Linux sends that signal when the *thread* that started the child exits,
not the process, and anyio closes idle request threads after a few seconds. So every child is
started from one thread that lives as long as Tamra.
"""

import ctypes
import logging
import os
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tamra.winjob import KillOnCloseJob

log = logging.getLogger(__name__)

# No console window for a child of the windowed app; the flag exists only on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PR_SET_PDEATHSIG = 1
_libc = ctypes.CDLL(None, use_errno=True) if sys.platform == "linux" else None
_spawner: ThreadPoolExecutor | None = None  # made on first use; its one thread never exits
_spawner_lock = threading.Lock()


def spawn(args: list[str], **popen_kwargs) -> "tuple[subprocess.Popen, KillOnCloseJob | None]":
    """Popen(args, **popen_kwargs) for a child that ends with Tamra. The second value is the
    Windows job to close() once the child is stopped: None when Windows refused the job (the
    child then runs untied, as before) and always None on Linux. OSError if it cannot start."""
    if sys.platform == "win32":
        proc = subprocess.Popen(args, creationflags=NO_WINDOW, **popen_kwargs)
        return proc, _kill_on_close(proc)
    preexec = _die_with(os.getpid())
    future = _spawner_thread().submit(subprocess.Popen, args, preexec_fn=preexec, **popen_kwargs)
    return future.result(), None


def _spawner_thread() -> ThreadPoolExecutor:
    global _spawner
    with _spawner_lock:
        if _spawner is None:
            _spawner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tamra-spawn")
        return _spawner


def _die_with(parent: int):
    """A preexec_fn: the child gets SIGKILL when the spawning thread (alive as long as Tamra)
    ends, and exits at once if Tamra already died before the signal was set."""

    def preexec() -> None:
        _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        if os.getppid() != parent:
            os._exit(1)

    return preexec


def _kill_on_close(proc: subprocess.Popen) -> "KillOnCloseJob | None":
    """Tie proc's lifetime to Tamra's. If Windows refuses, keep running without the job."""
    from tamra.winjob import KillOnCloseJob

    job = None
    try:
        job = KillOnCloseJob()
        job.add(proc)
        return job
    except OSError as e:
        if job is not None:
            job.close()
        log.warning("%s was not placed in a kill-on-close job: %s", Path(proc.args[0]).name, e)
        return None
