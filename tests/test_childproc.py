"""tamra.childproc: a child ends with Tamra (Ubuntu support spec §1.2)."""

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tamra.childproc import NO_WINDOW, spawn

linux = pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux's")


def test_spawn_runs_the_command(tmp_path):
    out = tmp_path / "out.txt"
    with out.open("wb") as f:
        proc, job = spawn([sys.executable, "-c", "print('hi')"], stdout=f)
        assert proc.wait(timeout=30) == 0
    if job is not None:
        job.close()
    assert out.read_text().strip() == "hi"


def test_a_missing_program_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        spawn([str(tmp_path / "missing")])


def test_no_window_is_a_windows_flag():
    assert NO_WINDOW == (subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)


def _alive(pid: int) -> bool:
    """Running; a zombie waiting for its new parent to reap it counts as ended."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


# A stand-in for Tamra: it starts `sleep` from a short-lived thread, as a request thread does.
HELPER = textwrap.dedent(
    """
    import threading, time
    from tamra.childproc import spawn
    started = {}
    thread = threading.Thread(target=lambda: started.update(proc=spawn(["sleep", "60"])[0]))
    thread.start()
    thread.join()
    print(started["proc"].pid, flush=True)
    time.sleep(60)
    """
)


@linux
def test_the_child_outlives_its_starting_thread_and_dies_with_tamra():
    helper = subprocess.Popen([sys.executable, "-c", HELPER], stdout=subprocess.PIPE, text=True)
    try:
        pid = int(helper.stdout.readline())
        time.sleep(1.0)  # the thread that called spawn() has ended
        assert _alive(pid)
        helper.kill()  # SIGKILL: "Tamra" gets no chance to clean up
        helper.wait(timeout=10)
        deadline = time.monotonic() + 10
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(pid)
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait()
