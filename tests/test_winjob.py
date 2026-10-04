import os
import subprocess
from pathlib import Path

from tamra.winjob import KillOnCloseJob

PING = Path(os.environ["SystemRoot"]) / "System32" / "PING.EXE"


def test_closing_the_job_kills_its_process():
    proc = subprocess.Popen(
        [str(PING), "-n", "60", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        job = KillOnCloseJob()
        job.add(proc)
        job.close()
        assert proc.wait(timeout=10) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_close_is_idempotent():
    job = KillOnCloseJob()
    job.close()
    job.close()
