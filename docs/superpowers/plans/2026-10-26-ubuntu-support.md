# Ubuntu Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tamra runs on Ubuntu 24.04+ as a desktop app installed from `tamra_<version>_amd64.deb`, while the Windows app keeps working exactly as before.

**Architecture:** Each OS difference is handled in the module that owns it (`sys.platform` checks). Two new modules hold the OS-specific process and desktop services (`childproc.py`, `desktop.py`). On Linux the window is pywebview's Qt backend with PySide6 (Qt WebEngine = Chromium). PyInstaller builds `dist/tamra/`, and `scripts/make_deb.py` wraps it into a `.deb` with a menu entry, icons and an AppArmor profile for the Chromium sandbox.

**Tech Stack:** Python 3.12 (uv), FastAPI, pywebview 6.2 + PySide6/qtpy (Linux only), llama.cpp b11344 Ubuntu Vulkan build, PyInstaller, dpkg-deb, GitHub Actions (`ubuntu-24.04`), React/TypeScript + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-26-ubuntu-support-design.md`

## Global Constraints

- Windows behavior must not change. Windows tests keep passing on Windows (`uv run pytest`, `npm --prefix ui test`).
- Target: Ubuntu 24.04 LTS and later, x86_64. Build and test on 24.04.
- Licenses: every dependency Apache-2.0-compatible; PySide6/Qt (LGPLv3) allowed; never PyQt5/PyQt6 (GPL); no AGPL/GPL.
- Assets are pinned with sha256 and never committed (`.models/`, `vendor/`).
- Linux data folder: `TAMRA_DATA_DIR`, else `$XDG_DATA_HOME/tamra` (absolute `XDG_DATA_HOME` only), else `~/.local/share/tamra`; kept `0700`. `mcp.json` is written `0600`.
- llama.cpp: `llama-b11344-bin-ubuntu-vulkan-x64.tar.gz`, sha256 `b8adfbb4eff636fcefb9574f52d8832d4ea7e9e3afbb8659d8ea538bad462b7a`.
- Python style: ruff (line length 100, rules E F I UP B), English comments matching the surrounding density, `uv run ruff check; uv run ruff format`.
- Every task ends with the full suite green on Windows **and** on Linux (WSL, Task 0), then a commit. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## How to run the Linux side (read before Task 1)

The repository lives on Windows. Linux checks run in the WSL distro `Ubuntu-24.04` on a copy of the working tree at `~/tamra-dev`, refreshed by `~/bin/tamra-sync` (Task 0). From PowerShell:

```powershell
wsl.exe -d Ubuntu-24.04 -- bash -lc "tamra-sync && cd ~/tamra-dev && uv run pytest -q"
```

`bash -lc` puts `~/bin` and `~/.local/bin` on `PATH`. Root commands use `wsl.exe -d Ubuntu-24.04 -u root -- ...` (the user approved package installs and installing the built `.deb` in this distro). Write multi-line Linux scripts to a file and run `wsl.exe -d Ubuntu-24.04 -- bash /mnt/c/...script.sh`: PowerShell mangles `$` inside inline `bash -lc` strings.

## File Structure

| File | Responsibility |
|---|---|
| `src/tamra/childproc.py` (new) | `spawn()`: start a child that dies with Tamra (Windows job / Linux `PR_SET_PDEATHSIG` from one long-lived thread); `NO_WINDOW` |
| `src/tamra/desktop.py` (new) | One instance per data folder, opening a path in its default app, the error dialog, `webview.start()` options for this platform |
| `src/tamra/paths.py` | Data folder per platform and its mode; `llama_server()` |
| `src/tamra/discovery.py` | `mcp.json` written `0600` |
| `src/tamra/app.py` | Uses `desktop` and `paths.llama_server()`; window icon per platform |
| `src/tamra/llm/llama_server.py`, `src/tamra/models/hardware.py` | Use `childproc`; RAM on Linux; Mesa GPU names |
| `src/tamra/drives.py`, `src/tamra/ingest/indexer.py`, `src/tamra/models/external.py`, `src/tamra/core.py`, `src/tamra/server.py` | `/media` removable check, case rule, Ollama service folder, path examples, `platform` in settings |
| `src/tamra/selfcheck.py`, `src/tamra/__main__.py` | `selfcheck --window` |
| `ui/src/...` | Platform-dependent wording |
| `scripts/fetch_assets.py`, `scripts/assets.json` | Per-platform llama.cpp, `.tar.gz` extraction |
| `packaging/tamra.spec`, `packaging/tamra.png`, `scripts/make_icon.py` | Linux bundle and icon |
| `scripts/build.sh`, `scripts/make_deb.py`, `packaging/linux/*` | Build the `.deb` |
| `scripts/exe_smoke.py` | Works on both platforms |
| `.github/workflows/ci.yml`, `.github/workflows/release.yml` | Ubuntu test, package and release jobs |
| `tests/...` | Run on both platforms |
| `README.md`, `CLAUDE.md`, `NOTICE`, specs | Docs |

---

### Task 0: Linux dev environment in WSL (no commit)

Already done in brainstorming (verify, do not redo): uv 0.13.1 at `~/.local/bin/uv` (from its GitHub release, sha256-checked), and these packages installed as root: `libgomp1 libnss3 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libasound2t64 libxkbfile1 xvfb zenity xdg-utils fonts-tlwg-garuda-ttf fonts-wqy-microhei`. A clone exists at `~/tamra-dev`.

- [ ] **Step 1: Write the sync helper** to `~/bin/tamra-sync` inside WSL (write the file from Windows to `\\wsl$\Ubuntu-24.04\home\<user>\bin\tamra-sync`, or with a heredoc in a script file):

```bash
#!/bin/bash
# Copy the Windows checkout's working tree (tracked and new files, never ignored ones) into
# ~/tamra-dev, and delete files that are gone on Windows. Ignored folders (.venv, node_modules,
# vendor, dist, build) stay Linux's own.
set -euo pipefail
src="/mnt/c/Users/Jirapat Chumaungphan/Documents/Project/ai_rag_application"
dst="$HOME/tamra-dev"
list() { git -c safe.directory='*' -C "$1" ls-files --cached --others --exclude-standard | sort; }
comm -13 <(list "$src") <(list "$dst") | while IFS= read -r f; do rm -f "$dst/$f"; done
(cd "$src" && git -c safe.directory='*' ls-files -z --cached --others --exclude-standard \
  | tar --null -T - -cf -) | tar -xf - -C "$dst"
```

Run: `chmod +x ~/bin/tamra-sync && tamra-sync && ls ~/tamra-dev/src/tamra`
Expected: the module list, no errors.

- [ ] **Step 2: Models for `-m assets` tests.** Copy the Windows `.models` once (about 1.1 GB):

```bash
cp -r "/mnt/c/Users/Jirapat Chumaungphan/Documents/Project/ai_rag_application/.models" ~/tamra-dev/.models
```

- [ ] **Step 3: Node 24 in user space** (for the UI build in Task 9). Download `node-v24.19.0-linux-x64.tar.xz` and `SHASUMS256.txt` from `https://nodejs.org/dist/v24.19.0/`, check with `grep node-v24.19.0-linux-x64.tar.xz SHASUMS256.txt | sha256sum -c`, extract to `~/.local/node`, and link `node`, `npm`, `npx` into `~/.local/bin`.

Run: `node --version && npm --version`
Expected: `v24.19.0` and an npm version.

- [ ] **Step 4: Baseline.** `cd ~/tamra-dev && uv sync --locked && uv run pytest -q`
Expected today: collection errors (`ctypes` has no `WinDLL`). Task 1 fixes them.

---

### Task 1: Children that end with Tamra (`childproc`)

**Files:**
- Create: `src/tamra/childproc.py`
- Modify: `src/tamra/llm/llama_server.py` (imports, `start()`, delete `_kill_on_close`)
- Modify: `src/tamra/models/hardware.py:66-72` (`creationflags`)
- Modify: `tests/conftest.py` (skip `test_winjob.py` off Windows)
- Modify: `tests/test_models_hardware.py:146`
- Test: `tests/test_childproc.py` (new)

**Interfaces:**
- Produces: `tamra.childproc.spawn(args: list[str], **popen_kwargs) -> tuple[subprocess.Popen, KillOnCloseJob | None]` (raises `OSError` if it cannot start); `tamra.childproc.NO_WINDOW: int`.

- [ ] **Step 1: Write the failing tests** in `tests/test_childproc.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run (Windows): `uv run pytest tests/test_childproc.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'tamra.childproc'`.

- [ ] **Step 3: Write `src/tamra/childproc.py`**

```python
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
```

- [ ] **Step 4: Use it in `src/tamra/llm/llama_server.py`.** Replace the import `from tamra.winjob import KillOnCloseJob` with:

```python
from typing import IO, TYPE_CHECKING

from tamra.childproc import spawn
from tamra.net import free_port

if TYPE_CHECKING:
    from tamra.winjob import KillOnCloseJob
```

(merge `TYPE_CHECKING` into the existing `from typing import IO` line). Change the annotation to `self._job: "KillOnCloseJob | None" = None`. In `start()`, replace the `subprocess.Popen(...)` call and the following `self._job = _kill_on_close(self._proc)` line with:

```python
                try:
                    self._proc, self._job = spawn(
                        self.args(gpu),
                        stdout=self._log,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                    )
                except OSError as e:
                    raise LlamaServerError(f"cannot launch {self.exe}: {e}") from e
```

Delete the module-level `_kill_on_close` function at the end of the file (it moved to `childproc`).

- [ ] **Step 5: `hardware.detect`.** In `src/tamra/models/hardware.py` add `from tamra.childproc import NO_WINDOW` and replace `creationflags=subprocess.CREATE_NO_WINDOW,` with `creationflags=NO_WINDOW,`. In `tests/test_models_hardware.py`, import `from tamra.childproc import NO_WINDOW` and change line 146 to `assert calls["kwargs"]["creationflags"] == NO_WINDOW`.

- [ ] **Step 6: Skip the Windows-only job test elsewhere.** At the top of `tests/conftest.py` (after the imports, add `import sys`):

```python
# Test modules for Windows-only code that cannot even be imported elsewhere.
collect_ignore = [] if sys.platform == "win32" else ["test_winjob.py"]
```

- [ ] **Step 7: Run the tests on both platforms**

Run (Windows): `uv run pytest tests/test_childproc.py tests/test_llama_server.py tests/test_models_hardware.py tests/test_winjob.py tests/test_selfcheck.py -q`
Expected: PASS (the `linux` test skipped).
Run (WSL): `tamra-sync && cd ~/tamra-dev && uv run pytest tests/test_childproc.py tests/test_llama_server.py tests/test_models_hardware.py tests/test_selfcheck.py -q`
Expected: PASS, including `test_the_child_outlives_its_starting_thread_and_dies_with_tamra`. Then `uv run pytest -q` collects every module (other failures are fixed by later tasks; note the list).

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check && uv run ruff format
git add src/tamra/childproc.py src/tamra/llm/llama_server.py src/tamra/models/hardware.py tests/conftest.py tests/test_childproc.py tests/test_models_hardware.py
git commit -m "feat: start llama-server through childproc, which ties it to Tamra on Windows and Linux"
```

---

### Task 2: Data folder, its modes, and the bundled llama-server path

**Files:**
- Modify: `src/tamra/paths.py`
- Modify: `src/tamra/discovery.py` (`write_discovery`)
- Modify: `src/tamra/app.py` (`build_core`), `src/tamra/__main__.py` (selfcheck), `tests/conftest.py` (`llama_exe`)
- Test: `tests/test_paths.py`, `tests/test_discovery.py`

**Interfaces:**
- Produces: `tamra.paths.llama_server() -> Path`; `data_dir()` unchanged signature.

- [ ] **Step 1: Write the failing tests.** In `tests/test_paths.py` add `import stat` and these markers, mark the three `LOCALAPPDATA` tests (`test_data_dir_defaults_to_localappdata`, `test_a_blank_override_falls_back_to_localappdata`, `test_a_missing_localappdata_says_what_to_do`) with `@windows`, and add:

```python
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows' data folder")
posix = pytest.mark.skipif(sys.platform == "win32", reason="Linux's data folder and modes")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@posix
def test_data_dir_defaults_to_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.data_dir() == tmp_path / "xdg" / "tamra"


@posix
def test_without_xdg_data_home_the_data_dir_is_in_local_share(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.data_dir() == tmp_path / ".local" / "share" / "tamra"


@posix
def test_a_relative_xdg_data_home_and_a_blank_override_are_ignored(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAMRA_DATA_DIR", "  ")
    monkeypatch.setenv("XDG_DATA_HOME", "rel")  # the XDG spec says to ignore a relative path
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert paths.data_dir() == tmp_path / "home" / ".local" / "share" / "tamra"


@posix
def test_the_default_data_dir_is_kept_private(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    folder = paths.data_dir()
    assert mode(folder) == 0o700
    folder.chmod(0o755)
    paths.data_dir()
    assert mode(folder) == 0o700


@posix
def test_a_new_override_is_private_and_an_existing_one_keeps_its_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "new"))
    assert mode(paths.data_dir()) == 0o700
    existing = tmp_path / "existing"
    existing.mkdir()
    existing.chmod(0o755)
    monkeypatch.setenv("TAMRA_DATA_DIR", str(existing))
    assert mode(paths.data_dir()) == 0o755  # the user chose this folder


def test_llama_server_is_this_platforms_bundled_build():
    name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    assert paths.llama_server() == paths.resource_dir() / "vendor" / "llama" / name
```

In `tests/test_discovery.py` add (with `import os, stat, sys` and `import pytest` as needed):

```python
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_file_is_readable_only_by_this_user_even_after_a_leftover(tmp_path):
    leftover = tmp_path / "mcp.json.tmp"
    leftover.write_text("old")
    leftover.chmod(0o644)
    write_discovery(tmp_path, 8765, "secret")
    assert stat.S_IMODE((tmp_path / "mcp.json").stat().st_mode) == 0o600
    assert read_discovery(tmp_path) == (8765, "secret")
```

- [ ] **Step 2: Run them to see them fail**

Run (WSL): `tamra-sync && cd ~/tamra-dev && uv run pytest tests/test_paths.py tests/test_discovery.py -q`
Expected: FAIL (`LOCALAPPDATA is not set`, `no attribute 'llama_server'`, mode `0o644`).

- [ ] **Step 3: Write `paths.py`** (replace `data_dir`, add the helpers and `llama_server`; `resource_dir` and `models_dir` stay):

```python
def data_dir() -> Path:
    """Per-user data folder: TAMRA_DATA_DIR if set, else %LOCALAPPDATA%\\Tamra on Windows and
    $XDG_DATA_HOME/tamra (default ~/.local/share/tamra) on Linux.

    On Linux a new folder would be readable by other users (0755), and this one holds the chats
    and the MCP token. So Tamra makes a folder it creates 0700, and keeps its default folder
    0700 on every call. An existing TAMRA_DATA_DIR keeps its mode: the user chose it."""
    override = os.environ.get("TAMRA_DATA_DIR", "").strip()
    base = Path(override).expanduser().absolute() if override else _default_data_dir()
    _make_dir(base, keep_private=not override)
    return base


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if not local:
            raise RuntimeError(
                "LOCALAPPDATA is not set; set TAMRA_DATA_DIR to the folder Tamra should use"
            )
        return Path(local) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    # The XDG spec: a relative XDG_DATA_HOME is invalid and is ignored.
    root = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local" / "share"
    return root / APP_NAME.lower()


def _make_dir(path: Path, keep_private: bool) -> None:
    if os.name == "nt":  # the per-user AppData folder is private already
        path.mkdir(parents=True, exist_ok=True)
        return
    existed = path.is_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if keep_private or not existed:
        path.chmod(0o700)


def llama_server() -> Path:
    """The bundled llama.cpp server (vendor/llama, from scripts/fetch_assets.py llama)."""
    name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    return resource_dir() / "vendor" / "llama" / name
```

- [ ] **Step 4: Write `discovery.write_discovery`**

```python
def write_discovery(folder: Path, port: int, token: str) -> None:
    """Write the file atomically (a reader sees the old content or the new, never half), readable
    only by this user even in a data folder that others can read."""
    path = folder / FILE_NAME
    temporary = path.with_name(FILE_NAME + ".tmp")
    temporary.unlink(missing_ok=True)  # a leftover could carry a wider mode
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"port": port, "token": token}))
    os.replace(temporary, path)
```

- [ ] **Step 5: Use `llama_server()`.** `app.build_core`: `from tamra.paths import data_dir, llama_server, models_dir, resource_dir` and pass `llama_server()` instead of `resource_dir() / "vendor" / "llama" / "llama-server.exe"`. `__main__` selfcheck: `from tamra.paths import llama_server` and pass `llama_server()`. `tests/conftest.py` fixture `llama_exe`: `from tamra.paths import llama_server` then `return _require(llama_server())`.

- [ ] **Step 6: Run the tests on both platforms**

Run (Windows): `uv run pytest tests/test_paths.py tests/test_discovery.py tests/test_app.py tests/test_selfcheck.py -q` → PASS.
Run (WSL): `tamra-sync && cd ~/tamra-dev && uv run pytest tests/test_paths.py tests/test_discovery.py -q` → PASS.

- [ ] **Step 7: Commit**

```bash
uv run ruff check && uv run ruff format
git add src/tamra/paths.py src/tamra/discovery.py src/tamra/app.py src/tamra/__main__.py tests/conftest.py tests/test_paths.py tests/test_discovery.py
git commit -m "feat: the Linux data folder under XDG_DATA_HOME, kept private, and one path to llama-server"
```

---

### Task 3: Desktop services (`desktop.py`): one instance, opening files, the error dialog

**Files:**
- Create: `src/tamra/desktop.py`
- Modify: `src/tamra/app.py` (remove `ctypes`, `hashlib`, `_kernel32`, `_close_handle`, `_single_instance`, `ERROR_ALREADY_EXISTS`, `_alert`; `open_data_folder`/`open_external` use `open_path`)
- Modify: `tests/test_app.py`
- Test: `tests/test_desktop.py` (new)

**Interfaces:**
- Produces: `single_instance(data_dir: Path) -> object | None`, `release_instance(handle: object) -> None`, `open_path(path: Path) -> None`, `alert(message: str) -> None` in `tamra.desktop`. `app.py` imports them as `_single_instance`, `_release_instance`, `open_path`, `_alert` so tests can patch `app.<name>`.

- [ ] **Step 1: Write the failing tests** in `tests/test_desktop.py`:

```python
"""tamra.desktop (Ubuntu support spec §1.3-1.5)."""

import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tamra import desktop

linux = pytest.mark.skipif(sys.platform != "linux", reason="Linux's desktop")


def test_one_instance_per_data_folder(tmp_path):
    first = desktop.single_instance(tmp_path)
    try:
        assert first is not None
        assert desktop.single_instance(tmp_path) is None
        (tmp_path / "other").mkdir()
        other = desktop.single_instance(tmp_path / "other")  # another data folder is independent
        assert other is not None
        desktop.release_instance(other)
    finally:
        desktop.release_instance(first)
    again = desktop.single_instance(tmp_path)  # releasing freed it
    assert again is not None
    desktop.release_instance(again)


@linux
def test_the_lock_file_is_private_and_not_inherited(tmp_path):
    handle = desktop.single_instance(tmp_path)
    try:
        import fcntl

        lock = tmp_path / "tamra.lock"
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600
        # Closed on exec: a llama-server that outlived Tamra must never hold Tamra's lock.
        assert fcntl.fcntl(handle, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
    finally:
        desktop.release_instance(handle)


@linux
def test_open_path_runs_xdg_open_without_a_shell(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **k: calls.append((args, k)))
    desktop.open_path(tmp_path / "a b.pdf")
    args, options = calls[0]
    assert args == ["xdg-open", str(tmp_path / "a b.pdf")]
    assert options["start_new_session"] is True
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL


@linux
def test_alert_uses_zenity_without_markup(monkeypatch):
    runs = []
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/zenity")
    monkeypatch.setattr(desktop.subprocess, "run", lambda args, **k: runs.append(args))
    desktop.alert("Tamra could not start: <b>x</b>")
    assert runs == [
        ["/usr/bin/zenity", "--error", "--no-markup", "--title=Tamra",
         "--text=Tamra could not start: <b>x</b>"]
    ]


@linux
def test_alert_without_zenity_writes_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    desktop.alert("Tamra is already running.")
    assert capsys.readouterr().err == "Tamra is already running.\n"
```

- [ ] **Step 2: Run them to see them fail**

Run (Windows): `uv run pytest tests/test_desktop.py -q`
Expected: FAIL, `ImportError: cannot import name 'desktop'`.

- [ ] **Step 3: Write `src/tamra/desktop.py`** (the Windows code moves here from `app.py` unchanged):

```python
"""What Tamra asks of the desktop: one Tamra per data folder, opening a file in its default
app, an error dialog, and how to start the window. Windows and Linux do each differently."""

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ERROR_ALREADY_EXISTS = 183
LOCK_FILE = "tamra.lock"


def single_instance(data_dir: Path) -> object | None:
    """Take the instance lock for this data folder; None when another Tamra already holds it.
    Release it with release_instance(); the OS also drops it when the process ends.

    Windows: a named mutex whose name carries a hash of the folder, so a run with its own
    TAMRA_DATA_DIR never collides. Linux: an flock on <data_dir>/tamra.lock."""
    if sys.platform == "win32":
        return _windows_mutex(data_dir)
    import fcntl

    fd = os.open(data_dir / LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def release_instance(handle: object) -> None:
    if sys.platform == "win32":
        _kernel32().CloseHandle(handle)
    else:
        os.close(handle)  # type: ignore[arg-type]


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _windows_mutex(data_dir: Path):
    digest = hashlib.sha1(str(data_dir.resolve()).lower().encode("utf-8")).hexdigest()[:12]
    ctypes.set_last_error(0)  # type: ignore[attr-defined]
    handle = _kernel32().CreateMutexW(None, 0, f"Local\\Tamra-{digest}")
    error = ctypes.get_last_error()  # type: ignore[attr-defined]
    if not handle:
        raise ctypes.WinError(error)  # type: ignore[attr-defined]
    if error == ERROR_ALREADY_EXISTS:
        release_instance(handle)
        return None
    return handle


def open_path(path: Path) -> None:
    """Open a file in its default app, or a folder in the file manager. Callers pass only paths
    they resolved themselves (data_dir() or the store), never one from a request."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    subprocess.Popen(
        ["xdg-open", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # the opened app outlives Tamra
    )


def alert(message: str) -> None:
    """An error dialog: the windowed app has no console to print to. On Linux zenity, else
    stderr (zenity is part of Ubuntu Desktop, and the .deb recommends it)."""
    if sys.platform == "win32":
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.MessageBoxW(None, message, "Tamra", 0x10)  # MB_ICONERROR
        return
    zenity = shutil.which("zenity")
    if zenity:
        try:
            subprocess.run(
                [zenity, "--error", "--no-markup", "--title=Tamra", f"--text={message}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        except OSError:
            pass
    if sys.stderr is not None:
        print(message, file=sys.stderr)
```

- [ ] **Step 4: Change `app.py`.** Remove `import ctypes`, `import hashlib`, `ERROR_ALREADY_EXISTS`, `_kernel32`, `_close_handle`, `_single_instance` and `_alert`. Add:

```python
from tamra.desktop import alert as _alert
from tamra.desktop import open_path
from tamra.desktop import release_instance as _release_instance
from tamra.desktop import single_instance as _single_instance
```

Change the mutex comment to `_instance_lock = None  # held for the process lifetime; the OS frees it when the process exits`, rename `_instance_mutex` to `_instance_lock` in `run()`, and replace `_close_handle(...)` with `_release_instance(...)`. The openers become:

```python
def open_data_folder() -> None:
    """Show Tamra's data folder in the file manager. The path is never taken from a request."""
    open_path(data_dir())


def open_external(path: Path) -> None:
    """Open a file in its default app. The server passes only paths it resolved from the store."""
    open_path(path)
```

- [ ] **Step 5: Update `tests/test_app.py`.** `test_open_data_folder_opens_only_the_data_directory` patches `monkeypatch.setattr(app, "open_path", opened.append)`. In `test_a_second_instance_for_the_same_data_folder_is_refused` and `test_run_refuses_a_second_instance` replace `app._close_handle` with `app._release_instance`, and create the other data folder first: `(tmp_path / "other").mkdir()` (the Linux lock file lives in the folder).

- [ ] **Step 6: Run on both platforms**

Run (Windows): `uv run pytest tests/test_desktop.py tests/test_app.py -q` → PASS (Linux tests skipped).
Run (WSL): `tamra-sync && cd ~/tamra-dev && uv run pytest tests/test_desktop.py tests/test_app.py -q` → PASS.

- [ ] **Step 7: Commit**

```bash
uv run ruff check && uv run ruff format
git add src/tamra/desktop.py src/tamra/app.py tests/test_desktop.py tests/test_app.py
git commit -m "feat: desktop services for Linux: an flock per data folder, xdg-open, and a zenity error dialog"
```

---

### Task 4: Hardware and file checks that differ on Linux

**Files:**
- Modify: `src/tamra/models/hardware.py` (`_ram_gb`, `_is_integrated`)
- Modify: `src/tamra/drives.py`
- Modify: `src/tamra/ingest/indexer.py` (`_under`)
- Modify: `src/tamra/models/external.py` (`_ollama`)
- Modify: `src/tamra/core.py` (`_checked_folder` messages), `src/tamra/server.py` (`import_model` message)
- Test: `tests/test_models_hardware.py`, `tests/test_drives.py`, `tests/test_indexer.py`, `tests/test_external.py`

- [ ] **Step 1: Write the failing tests.**

`tests/test_models_hardware.py`, a new parametrized test beside `test_recommend_classifies_discrete_and_integrated_names`:

```python
@pytest.mark.parametrize(
    "name, vram_gb, tier",
    [
        ("AMD Radeon 780M (RADV PHOENIX)", 16, "small"),
        ("AMD Radeon Graphics (RADV RENOIR)", 16, "small"),
        ("AMD Radeon RX 7800 XT (RADV NAVI32)", 16, "large"),
        ("AMD Radeon Pro W7800 (RADV NAVI31)", 32, "large"),
        ("Intel(R) Arc(tm) A770 Graphics (DG2)", 16, "large"),
        ("Intel(R) Graphics (RPL-P)", 16, "small"),
        ("NVIDIA GeForce RTX 4060 (NVK AD107)", 8, "medium"),
    ],
)
def test_recommend_classifies_mesa_names(catalog, name, vram_gb, tier):
    assert recommend(hw(gpu(name, vram_gb)), catalog) == tier


@pytest.mark.skipif(sys.platform == "win32", reason="sysconf")
def test_ram_on_linux_is_the_physical_memory():
    expected = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3
    assert hardware._ram_gb() == pytest.approx(expected)
```

`tests/test_drives.py`: mark the three `_drive_type` tests (`test_only_removable_and_cd_drives_count_as_removable`, `test_the_drive_root_is_what_windows_is_asked_about`, `test_a_failing_check_counts_as_a_fixed_drive`) with `@windows = pytest.mark.skipif(sys.platform != "win32", reason="asks Windows")` and add:

```python
linux = pytest.mark.skipif(sys.platform != "linux", reason="Linux mount points")


@linux
@pytest.mark.parametrize(
    ("path", "removable"),
    [
        ("/media/me/USB/m.gguf", True),
        ("/run/media/me/USB/m.gguf", True),
        ("/home/me/Models/m.gguf", False),
        ("/mediafiles/m.gguf", False),
    ],
)
def test_files_where_ubuntu_mounts_usb_drives_count_as_removable(path, removable):
    assert is_removable(Path(path)) is removable


@linux
def test_a_link_into_media_counts_as_removable(tmp_path, monkeypatch):
    monkeypatch.setattr(drives.os.path, "realpath", lambda p: "/media/me/USB/m.gguf")
    assert is_removable(tmp_path / "m.gguf") is True
```

`tests/test_external.py` (add `import sys` and `from tamra.models import external`; `ollama_model`, `HEX_A` and `scan` are already in this file). The autouse fixture keeps this machine's real Ollama service folder out of every test in the file:

```python
@pytest.fixture(autouse=True)
def _no_ollama_service(monkeypatch, tmp_path):
    monkeypatch.setattr(external, "_OLLAMA_SERVICE", tmp_path / "no-service")


@pytest.mark.skipif(sys.platform != "linux", reason="Ollama's Linux service folder")
def test_ollama_installed_as_a_service_is_found_after_the_users_own(tmp_path, monkeypatch):
    service = tmp_path / "service"
    blob = ollama_model(service, "qwen3", "4b", HEX_A)
    monkeypatch.setattr(external, "_OLLAMA_SERVICE", service)
    assert scan(tmp_path, {}) == [ExternalModel("ollama", "qwen3:4b", blob, HEX_A)]


@pytest.mark.skipif(sys.platform != "linux", reason="Ollama's Linux service folder")
def test_ollama_models_set_by_the_user_replace_the_service_folder(tmp_path, monkeypatch):
    ollama_model(tmp_path / "service", "qwen3", "4b", HEX_A)
    monkeypatch.setattr(external, "_OLLAMA_SERVICE", tmp_path / "service")
    assert scan(tmp_path, {"OLLAMA_MODELS": str(tmp_path / "elsewhere")}) == []
```

`tests/test_indexer.py`: a case test next to the other `_under`/reconcile tests:

```python
@pytest.mark.skipif(sys.platform == "win32", reason="Linux paths keep their case")
def test_on_linux_a_folder_that_differs_only_in_case_is_another_folder():
    assert indexer_module._under("Docs/a.md", ["docs"]) is False
    assert indexer_module._under("docs/a.md", ["docs"]) is True
```

- [ ] **Step 2: Run them to see them fail**

Run (WSL): `tamra-sync && cd ~/tamra-dev && uv run pytest tests/test_models_hardware.py tests/test_drives.py tests/test_external.py -q -k "mesa or linux or media or service or ram"`
Expected: FAIL (RADV iGPUs counted as discrete, `/media` not removable, `_OLLAMA_SERVICE` missing, `_ram_gb` uses `windll`).

- [ ] **Step 3: `hardware.py`.** Add `import os`, `import sys`. `_ram_gb` starts with:

```python
def _ram_gb() -> float:
    if sys.platform != "win32":
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3
```

(the Windows `MemoryStatus` code stays below it). Replace the GPU rules:

```python
_INTEL_DISCRETE = re.compile(r"Arc\(TM\) [AB]\d{3}", re.IGNORECASE)  # Mesa writes "Arc(tm)"
_AMD_DISCRETE = re.compile(r"\b(Pro|RX)\b")


def _is_integrated(gpu: Gpu) -> bool:
    """Intel (except Arc A/B cards) and AMD's integrated Radeons (except Pro/RX) are iGPUs.

    Windows names an AMD iGPU "AMD Radeon(TM) 780M Graphics"; Mesa names every AMD GPU
    "AMD Radeon ... (RADV <chip>)". llama-server --list-devices does not say which memory is
    shared, so the name is all there is."""
    name = gpu.name
    if "Intel" in name:
        return not _INTEL_DISCRETE.search(name)
    if ("Radeon(TM)" in name and "Graphics" in name) or "(RADV " in name:
        return not _AMD_DISCRETE.search(name)
    return False
```

- [ ] **Step 4: `drives.py`**

```python
"""Which kind of drive a path is on, so a model on a USB stick is copied rather than linked."""

import ctypes
import os
import sys
from pathlib import Path

_REMOVABLE, _CDROM = 2, 5  # GetDriveTypeW results
# Where Ubuntu's udisks2 mounts USB drives and discs.
_LINUX_REMOVABLE_ROOTS = (Path("/media"), Path("/run/media"))


def _drive_type(root: str) -> int:
    return ctypes.windll.kernel32.GetDriveTypeW(root)  # type: ignore[attr-defined]


def is_removable(path: Path) -> bool:
    """True for a path on a removable or CD-ROM drive, which may be gone the next time Tamra
    starts. Linux: a path under /media or /run/media. False when Windows cannot say."""
    if sys.platform != "win32":
        real = Path(os.path.realpath(path))
        return any(real.is_relative_to(root) and real != root for root in _LINUX_REMOVABLE_ROOTS)
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    try:
        return _drive_type(root) in (_REMOVABLE, _CDROM)
    except (AttributeError, OSError):
        return False
```

- [ ] **Step 5: `indexer._under`** (add `import sys`):

```python
def _under(rel_path: str, places: list[str]) -> bool:
    def fold(path: str) -> str:
        return path.casefold() if sys.platform == "win32" else path  # Windows ignores case

    rel = fold(rel_path)
    return any(p == "." or rel == fold(p) or rel.startswith(fold(p) + "/") for p in places)
```

- [ ] **Step 6: `external._ollama`** (add `import sys`):

```python
# Ollama's Linux installer runs it as the "ollama" user, whose models live here.
_OLLAMA_SERVICE = Path("/usr/share/ollama/.ollama/models")


def _ollama(home: Path, env: Mapping[str, str]) -> list[ExternalModel]:
    configured = env.get("OLLAMA_MODELS")
    roots = [Path(configured)] if configured else [home / ".ollama" / "models"]
    if not configured and sys.platform == "linux":
        roots.append(_OLLAMA_SERVICE)
    names: dict[Path, str] = {}  # blob -> model:tag
    for root in roots:
        for manifest in [*_files(root / "manifests"), *_files(root / "manifests-v2")]:
            try:
                blob = _model_blob(root, manifest)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration) as e:
                log.debug("skipping the Ollama manifest %s: %s", manifest, e)
                continue
            if blob is None:
                continue
            name = f"{manifest.parent.name}:{manifest.name}"
            # Prefer a tag that says the size.
            if names.get(blob, ":latest").endswith(":latest"):
                names[blob] = name
    return [
        ExternalModel("ollama", name, blob, blob.name.removeprefix("sha256-"))
        for blob, name in names.items()
        if _is_gguf(blob)
    ]
```

(`_files` already returns `[]` for a folder it cannot read, such as a service folder without read access.)

- [ ] **Step 7: Path examples.** `core.py` (add `import sys`), near the top:

```python
_EXAMPLE_FOLDER = "C:\\Users\\me\\Documents" if sys.platform == "win32" else "/home/me/Documents"
```

and both `FolderMissing("Choose a full folder path, for example C:\\Users\\me\\Documents.")` become `FolderMissing(f"Choose a full folder path, for example {_EXAMPLE_FOLDER}.")`. `server.py` (has `import sys`): `_EXAMPLE_MODEL = "C:\\Models\\m.gguf" if sys.platform == "win32" else "/home/me/Models/m.gguf"` and the import message becomes `f"Choose a full file path, for example {_EXAMPLE_MODEL}."`.

- [ ] **Step 8: Run on both platforms**

Run (Windows): `uv run pytest tests/test_models_hardware.py tests/test_drives.py tests/test_external.py tests/test_core.py tests/test_server_settings.py -q` → PASS.
Run (WSL): same files → the new tests PASS (unrelated Windows-shaped tests are Task 5).

- [ ] **Step 9: Commit**

```bash
uv run ruff check && uv run ruff format
git add src/tamra/models/hardware.py src/tamra/drives.py src/tamra/ingest/indexer.py src/tamra/models/external.py src/tamra/core.py src/tamra/server.py tests/test_models_hardware.py tests/test_drives.py tests/test_external.py tests/test_indexer.py
git commit -m "feat: Linux RAM, Mesa GPU names, /media drives, case-sensitive paths, Ollama's service folder"
```

---

### Task 5: The test suite runs on Linux

Found by running the suite in WSL during brainstorming. The causes:
1. `tests/docgen.py` writes PDF text with `ctypes.create_unicode_buffer`, which is UTF-32 on Linux; pdfium reads UTF-16, so only the first character arrived. It also hard-codes `C:/Windows/Fonts`.
2. Tests of Windows-only behavior: backslash paths, drive letters, UNC paths, case-insensitive model ids and links, `_winapi.CreateJunction`, `FILE_ATTRIBUTE_HIDDEN`.

**Files:** `tests/docgen.py`, `tests/test_indexer.py`, `tests/test_viewer.py`, `tests/test_server_attribution.py`, `tests/test_server_settings.py`, `tests/test_settings.py`, `tests/test_core.py`

- [ ] **Step 1: `docgen.py` fonts and UTF-16.** Replace the docstring and font constants:

```python
"""Build small PDF and DOCX fixtures. Uses system fonts: Tahoma and Microsoft YaHei on Windows,
Garuda and WenQuanYi Micro Hei on Ubuntu (fonts-tlwg-garuda-ttf, fonts-wqy-microhei)."""

import ctypes
import sys
from pathlib import Path
...
if sys.platform == "win32":
    LATIN_THAI = Path("C:/Windows/Fonts/tahoma.ttf")
    CJK = Path("C:/Windows/Fonts/msyh.ttc")
else:
    LATIN_THAI = Path("/usr/share/fonts/truetype/tlwg/Garuda.ttf")
    CJK = Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")
```

(`FONTS` is used only by these two lines; delete it. Check with `grep -rn "docgen import\|FONTS" tests`.) In `make_pdf` replace the two lines that build `text`:

```python
                utf16 = (line + "\0").encode("utf-16-le")  # pdfium's FPDF_WIDESTRING
                text = (ctypes.c_ushort * (len(utf16) // 2)).from_buffer_copy(utf16)
                pdfium_c.FPDFText_SetText(
                    obj, ctypes.cast(text, ctypes.POINTER(pdfium_c.FPDF_WCHAR))
                )
```

- [ ] **Step 2: `test_indexer.py`.** Remove `import _winapi` and `import ctypes` from the top. In `test_scan_skips_lock_hidden_dot_and_unsupported_files`, set the hidden attribute only on Windows:

```python
    hidden = tmp_path / "hidden.md"
    hidden.write_text("x", encoding="utf-8")
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(hidden), 0x2)  # FILE_ATTRIBUTE_HIDDEN
        expected = {"keep.md"}
    else:
        expected = {"keep.md", "hidden.md"}  # Linux has no hidden attribute, only dot files
    assert set(scan_folder(tmp_path)[0]) == expected
```

In `test_scan_skips_directory_junctions_and_symlinks`, make the junction part Windows-only:

```python
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(folder / "junction"))
        assert (folder / "junction" / "secret.md").exists()  # the junction really leads outside
        assert set(scan_folder(folder)[0]) == {"real/kept.md"}
```

(the symlink part below stays for both platforms). Add `import sys` at the top.

- [ ] **Step 3: Windows path shapes.**
- `test_viewer.py::test_resolve_file_refuses_escapes`: loop over `("../x.txt", "../docs-other/x.txt", str(tmp_path / "x.txt"))` plus `"..\\..\\x"` only on Windows: `escapes = [...]; if sys.platform == "win32": escapes.append("..\\..\\x")` (on Linux a backslash is a file-name character, so that name stays inside the folder).
- `test_viewer.py::test_resolve_file_refuses_drive_relative_unc_and_nul_paths`: run the `D:x.txt` / UNC loop only on Windows; the NUL check stays for both.
- `test_server_attribution.py::test_open_refuses_a_path_outside_the_folder`: `env.add_file("../outside.txt")` (true on both platforms; on Windows a separate case with `"..\\outside.txt"` is kept by parametrizing `rel` over `["../outside.txt"] + (["..\\outside.txt"] if sys.platform == "win32" else [])`).
- `test_server_settings.py::test_import_accepts_a_unc_path_shape`: `@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are Windows'")`.
- `test_settings.py`: replace `"D:\\Models\\a.gguf"` (in `test_every_field_round_trips` and `LINK`) with a module constant `ABSOLUTE_MODEL = str(Path("/Models/a.gguf").absolute())` (`C:\Models\a.gguf` on Windows, `/Models/a.gguf` on Linux). Keep the invalid-value case `"D:\\a.gguf"` (a string, not a list, is invalid on both).

- [ ] **Step 4: Case-insensitive ids and links are Windows behavior.**
- `test_core.py::test_the_local_model_is_the_selected_one_when_installed`: the `"import:LOOSE.gguf"` lines run only on Windows; on Linux assert `c.apply_settings({"local_model_id": "import:loose.gguf"})` selects `"loose"`.
- `test_core.py::test_unlinking_forgets_the_file_but_leaves_it_alone`: `c.unlink_model(str(source).upper() if sys.platform == "win32" else str(source))`, then the `KeyError` check uses `str(source)` on Windows and `str(source).upper()` on Linux (a path in other case is another file there).
- `test_server_settings.py::test_active_model_is_the_selected_one_when_it_is_installed`: use `"import:mine.GGUF"` only on Windows and `"import:Mine.gguf"` on Linux; the expected payload is the same.

- [ ] **Step 5: Run the whole suite on both platforms**

Run (WSL): `tamra-sync && cd ~/tamra-dev && uv run pytest -q`
Expected: all pass (skips only for Windows-only and asset tests).
Run (Windows): `uv run pytest -q`
Expected: all pass, the same count of passing tests as before plus the new ones.

- [ ] **Step 6: Commit**

```bash
uv run ruff check && uv run ruff format
git add tests
git commit -m "test: run the suite on Linux: UTF-16 PDF text, Linux fonts, Windows-only path cases"
```

---

### Task 6: Platform wording in the UI

**Files:**
- Modify: `src/tamra/server.py` (`_settings_payload`), `tests/test_server_settings.py`
- Modify: `ui/src/types.ts`, `ui/src/settings.ts`, `ui/src/strings/en.ts`, `ui/src/strings/th.ts`, `ui/src/SettingsCloud.tsx`, `ui/src/SettingsGeneral.tsx`, `ui/src/SettingsLocal.tsx`, `ui/src/FolderField.tsx`
- Test: `ui/src/SettingsPage.test.tsx`, `ui/src/i18n.test.ts`

**Interfaces:**
- Produces: `GET /api/settings` → `"platform": "windows" | "linux"` (read-only, like `data_dir`).

- [ ] **Step 1: Server test** in `tests/test_server_settings.py` (next to the `data_dir` payload test):

```python
def test_settings_say_which_platform_tamra_runs_on(env):
    client, _, _ = env
    expected = "windows" if sys.platform == "win32" else "linux"
    assert client.get("/api/settings", headers=AUTH).json()["platform"] == expected
    response = client.put("/api/settings", headers=AUTH, json={"platform": "linux"})
    assert response.status_code == 400  # read-only, like data_dir
```

- [ ] **Step 2: Server.** In `_settings_payload` add `"platform": "windows" if sys.platform == "win32" else "linux",  # read-only: some wording differs`. In `secrets.py` the docstring's first line becomes "API keys live only in the OS credential store (Windows Credential Manager, or the Secret Service such as GNOME Keyring on Linux), never in files." Run `uv run pytest tests/test_server_settings.py -q` → PASS (PUT already refuses unknown fields with 400; if it does not, the test shows it).

- [ ] **Step 3: UI types and defaults.** `types.ts`: add to `Settings` `/** The core's OS: names the key store and path examples (read-only). */ platform: "windows" | "linux";` and add `"platform"` to the `Omit<...>` of `SettingsChanges`. `settings.ts` defaults: `platform: "windows", // until the core answers`.

- [ ] **Step 4: Strings.** `en.ts`:

```ts
  "folder.pathPlaceholder.linux": "Full folder path, like /home/me/Documents",
  "settings.importPathPlaceholder.linux": "Full path to a .gguf file, like /home/me/Models/model.gguf",
  "settings.apiKeyNote.linux": "Stored in GNOME Keyring, never in Tamra's files.",
  "settings.removeKeyText.linux":
    "The key is deleted from GNOME Keyring. Paste it again to use this provider.",
  "settings.theme.system": "Match system",
```

`th.ts`:

```ts
  "folder.pathPlaceholder.linux": "ใส่ที่อยู่โฟลเดอร์แบบเต็ม เช่น /home/me/Documents",
  "settings.importPathPlaceholder.linux": "ใส่ที่อยู่ไฟล์ .gguf แบบเต็ม เช่น /home/me/Models/model.gguf",
  "settings.apiKeyNote.linux": "เก็บไว้ใน GNOME Keyring ไม่เก็บในไฟล์ของ Tamra",
  "settings.removeKeyText.linux":
    "คีย์จะถูกลบออกจาก GNOME Keyring หากต้องการใช้ผู้ให้บริการนี้อีก ต้องวางคีย์ใหม่",
  "settings.theme.system": "ตามระบบ",
```

(each `.linux` key placed right after its Windows key; `settings.theme.system` replaces the old value).

- [ ] **Step 5: Components.** Each picks the key by `settings.platform` from `useSettings()`:
- `SettingsCloud.tsx`: `t(settings.platform === "linux" ? "settings.apiKeyNote.linux" : "settings.apiKeyNote")` and the same for `settings.removeKeyText` (line ~225).
- `SettingsLocal.tsx` (line ~277): the same for `settings.importPathPlaceholder`.
- `FolderField.tsx`: add `const { settings } = useSettings();` (import from `./settings`) and the same for `folder.pathPlaceholder`.
- `SettingsGeneral.tsx`: replace the constant with

```ts
// Shown until the core reports the real path.
const DEFAULT_DATA_FOLDER = { windows: "%LOCALAPPDATA%\\Tamra", linux: "~/.local/share/tamra" };
```

and use `settings.data_dir || DEFAULT_DATA_FOLDER[settings.platform]`.

- [ ] **Step 6: UI tests.** In `SettingsPage.test.tsx` add:

```tsx
  it("names GNOME Keyring on Linux", async () => {
    await mountPage({ tab: "model", settings: { platform: "linux", mode: "api" } });
    expect(text()).toContain("GNOME Keyring");
    expect(text()).not.toContain("Windows Credential Manager");
  });
```

(use the tab name and settings the existing API-key tests in this file use to show the key field, if they differ). In `i18n.test.ts`, extend "keep the backslashes of the Windows path example" with `expect(translate(language, "folder.pathPlaceholder.linux"), language).toContain("/home/me/Documents");`.

- [ ] **Step 7: Run**

Run: `npm --prefix ui test` → PASS. `npm --prefix ui run build` → no type errors.

- [ ] **Step 8: Commit**

```bash
git add src/tamra/server.py tests/test_server_settings.py ui/src
git commit -m "feat: the UI names GNOME Keyring and Linux path examples on Linux"
```

---

### Task 7: The window on Linux (Qt WebEngine) and `selfcheck --window`

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Modify: `src/tamra/desktop.py` (`window_options`), `src/tamra/app.py` (`_show_window`)
- Modify: `scripts/make_icon.py`; Create: `packaging/tamra.png`
- Modify: `src/tamra/selfcheck.py`, `src/tamra/__main__.py`
- Test: `tests/test_app.py`, `tests/test_desktop.py`, `tests/test_selfcheck.py`

**Interfaces:**
- Produces: `tamra.desktop.window_options() -> dict` (extra `webview.start()` keywords); `run_selfcheck(..., ui_dir: Path | None = None)`; CLI `tamra selfcheck --window`.

- [ ] **Step 1: Dependency.** In `pyproject.toml` `dependencies`, after `"pywebview>=5.3",` add `"pywebview[pyside6]>=6.2; sys_platform == 'linux'",  # Qt WebEngine: Chromium, as WebView2 on Windows`. Run `uv lock` (Windows), then `uv sync` on both platforms. Check: `uv tree --package pywebview` on Linux lists `pyside6` and `qtpy`; nothing named `pyqt`. Check the licenses: `uv run python -c "import importlib.metadata as m; print({d: m.metadata(d).get('License-Expression') or m.metadata(d).get('License') for d in ('PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'QtPy')})"` → LGPL-3.0 for the Qt ones, MIT for QtPy.

- [ ] **Step 2: Failing tests.** `tests/test_desktop.py`:

```python
@linux
def test_the_linux_window_is_qt_and_matches_the_desktop_file(monkeypatch):
    monkeypatch.delenv("QT_API", raising=False)
    assert desktop.window_options() == {"gui": "qt"}
    assert desktop.os.environ["QT_API"] == "pyside6"
    from qtpy.QtGui import QGuiApplication

    assert QGuiApplication.desktopFileName() == "tamra"  # GNOME shows tamra.desktop's icon


@pytest.mark.skipif(sys.platform != "win32", reason="WebView2")
def test_the_windows_window_needs_no_options():
    assert desktop.window_options() == {}
```

`tests/test_app.py`: in the three `_show_window` tests add `monkeypatch.setattr(app, "window_options", lambda: {})`, and change the icon test's assertion to `assert started["icon"].endswith("tamra.ico" if sys.platform == "win32" else "tamra.png")` (add `import sys`). Add:

```python
def test_the_window_uses_the_platform_options(monkeypatch):
    started = {}
    monkeypatch.setattr(webview, "create_window", lambda *a, **k: FakeWindow(None))
    monkeypatch.setattr(webview, "start", lambda **k: started.update(k))
    monkeypatch.setattr(app, "window_options", lambda: {"gui": "qt"})
    app._show_window(8765, "t", app.FolderPicker())
    assert started["gui"] == "qt"
```

`tests/test_selfcheck.py`:

```python
class DrawnWindow:
    def __init__(self):
        self.destroyed = False

    def evaluate_js(self, script):
        return True

    def destroy(self):
        self.destroyed = True


def test_the_window_check_opens_the_ui_and_closes_the_window(tmp_path, monkeypatch):
    import webview

    (tmp_path / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    window = DrawnWindow()
    opened = {}
    monkeypatch.setattr(webview, "create_window", lambda title, url, **k: opened.update(url=url) or window)
    monkeypatch.setattr(webview, "start", lambda func, w, **k: opened.update(k) or func(w))
    monkeypatch.setattr("tamra.desktop.window_options", lambda: {})
    report = run_selfcheck(None, None, tmp_path / "x.exe", tmp_path, ui_dir=tmp_path)
    assert report["checks"]["window"]["ok"] is True
    assert opened["url"] == str(tmp_path / "index.html") and opened["http_server"] is True
    assert window.destroyed


def test_the_window_check_reports_a_missing_ui(tmp_path):
    report = run_selfcheck(None, None, tmp_path / "x.exe", tmp_path, ui_dir=tmp_path / "none")
    assert report["ok"] is False
    assert "index.html" in report["checks"]["window"]["detail"]
```

- [ ] **Step 3: Run them to see them fail.** `uv run pytest tests/test_desktop.py tests/test_app.py tests/test_selfcheck.py -q` → FAIL (`window_options` and `ui_dir` missing).

- [ ] **Step 4: `desktop.window_options`**

```python
def window_options() -> dict:
    """Extra webview.start() options for this platform. Linux: pywebview's Qt backend (Qt
    WebEngine is Chromium, as WebView2 is on Windows), with the desktop file name GNOME uses to
    give the window tamra.desktop's icon. setDesktopFileName is static: no QApplication yet."""
    if sys.platform != "linux":
        return {}
    os.environ.setdefault("QT_API", "pyside6")
    from qtpy.QtGui import QGuiApplication

    QGuiApplication.setDesktopFileName("tamra")
    return {"gui": "qt"}
```

- [ ] **Step 5: `app._show_window`.** `from tamra.desktop import window_options`; the last lines become:

```python
    # pywebview otherwise uses sys.executable's icon, which is python.exe's when run from source.
    icon = "tamra.ico" if sys.platform == "win32" else "tamra.png"
    webview.start(icon=str(resource_dir() / "packaging" / icon), **window_options())
```

(`import sys` in `app.py`).

- [ ] **Step 6: The PNG icon.** In `scripts/make_icon.py` add `PNG = OUT.with_suffix(".png")  # the Linux window and menu icon` and at the end of `main()`:

```python
    big.save(PNG)
    print(f"wrote {PNG}")
```

Update the docstring's first line to "Render packaging/tamra.ico (Windows) and packaging/tamra.png (Linux, 256 px) from the logo in ui/src/logo.svg." Run `uv run scripts/make_icon.py`; `git status` shows `packaging/tamra.png` new and `tamra.ico` unchanged (if `tamra.ico` changed, restore it with `git checkout packaging/tamra.ico`: the ico must stay byte-identical).

- [ ] **Step 7: `selfcheck --window`.** In `selfcheck.py`:

```python
DRAWN = 'document.getElementById("root").childElementCount > 0'


def _check_window(ui_dir: Path, timeout: float = 30.0) -> dict:
    """Open a real window on the bundled UI and wait until React has drawn into #root: the web
    engine, its sandbox and the UI files all work. The window closes itself."""
    index = ui_dir / "index.html"
    if not index.is_file():
        return {"ok": False, "detail": f"missing {index}"}
    import webview

    from tamra.desktop import window_options

    state = {"ok": False, "detail": f"the page did not draw within {timeout:.0f} s"}

    def probe(window) -> None:
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                try:
                    if window.evaluate_js(DRAWN):
                        state.update(ok=True, detail="drawn")
                        return
                except Exception as e:  # still loading, or the engine failed
                    state["detail"] = f"{type(e).__name__}: {e}"
                time.sleep(0.25)
        finally:
            window.destroy()

    window = webview.create_window("Tamra selfcheck", str(index), width=800, height=600)
    webview.start(probe, window, http_server=True, **window_options())
    return state
```

`run_selfcheck` gains `ui_dir: Path | None = None` (docstring: "With ui_dir, also open a window on it.") and `if ui_dir is not None: checks["window"] = _guard(lambda: _check_window(ui_dir))`. In `__main__`: `sc.add_argument("--window", action="store_true", help="also open a window on the bundled UI")` and pass `ui_dir=resource_dir() / "ui" / "dist" if args.window else None`.

- [ ] **Step 8: Run on both platforms**

Run (Windows): `uv run pytest -q` → PASS.
Run (WSL): `tamra-sync && cd ~/tamra-dev && uv sync && uv run pytest -q` → PASS.
Real window (WSL): build the UI on Windows (`npm --prefix ui run build`), `tamra-sync` does not copy `ui/dist` (ignored), so copy it: `cp -r "/mnt/c/.../ui/dist" ~/tamra-dev/ui/dist`, then `cd ~/tamra-dev && xvfb-run -a uv run tamra selfcheck --window --report /tmp/w.json; cat /tmp/w.json`
Expected: `"window": {"ok": true, "detail": "drawn"}` (Mesa/EGL warnings under Xvfb are harmless).
Real app (WSL, WSLg shows the window on the Windows desktop): `cd ~/tamra-dev && TAMRA_DATA_DIR=/tmp/tamra-try uv run tamra` → the Tamra window opens with the UI; close it. (Running from source has no AppArmor profile; WSL does not restrict user namespaces, so the sandbox works here.)

- [ ] **Step 9: Commit**

```bash
uv run ruff check && uv run ruff format
git add pyproject.toml uv.lock src/tamra/desktop.py src/tamra/app.py src/tamra/selfcheck.py src/tamra/__main__.py scripts/make_icon.py packaging/tamra.png tests/test_desktop.py tests/test_app.py tests/test_selfcheck.py
git commit -m "feat: the Linux window on Qt WebEngine, and selfcheck --window"
```

---

### Task 8: llama.cpp for Linux in the asset pins

**Files:**
- Modify: `scripts/assets.json`, `scripts/fetch_assets.py`
- Test: `tests/test_fetch_assets.py` (new; `scripts` is on pytest's `pythonpath`)

**Interfaces:**
- Produces: `fetch_assets.select(assets: dict, names: list[str], platform: str) -> list[str]`, `fetch_assets.extract(archive: Path, target: Path) -> None`. `fetch_assets.py llama` keeps working for `build.ps1`.

- [ ] **Step 1: `assets.json`.** Rename the `"llama"` key to `"llama-windows"` and add `"platform": "win32"` to it. Add:

```json
  "llama-linux": {
    "platform": "linux",
    "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11344/llama-b11344-bin-ubuntu-vulkan-x64.tar.gz",
    "sha256": "b8adfbb4eff636fcefb9574f52d8832d4ea7e9e3afbb8659d8ea538bad462b7a",
    "dest": "vendor/downloads/llama-b11344-bin-ubuntu-vulkan-x64.tar.gz",
    "extract_to": "vendor/llama"
  },
```

- [ ] **Step 2: Failing tests** in `tests/test_fetch_assets.py`:

```python
"""scripts/fetch_assets.py: this platform's assets, and archives unpacked safely."""

import io
import sys
import tarfile
from pathlib import Path

import pytest
from fetch_assets import extract, select

ASSETS = {
    "llama-windows": {"platform": "win32"},
    "llama-linux": {"platform": "linux"},
    "bge-m3-model": {},
}


def test_llama_means_this_platforms_build():
    assert select(ASSETS, ["llama"], "win32") == ["llama-windows"]
    assert select(ASSETS, ["llama", "bge-m3-model"], "linux") == ["llama-linux", "bge-m3-model"]


def test_no_names_means_every_asset_for_this_platform():
    assert select(ASSETS, [], "linux") == ["llama-linux", "bge-m3-model"]


def test_an_unknown_name_is_refused():
    with pytest.raises(KeyError):
        select(ASSETS, ["nope"], "linux")


def _tar(path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for info, data in members:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def _file(name: str, data: bytes, mode: int = 0o644) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), mode
    return info, data


def test_a_single_top_folder_is_dropped_and_modes_and_links_are_kept(tmp_path):
    top = tarfile.TarInfo("llama-b1/")
    top.type = tarfile.DIRTYPE
    link = tarfile.TarInfo("llama-b1/libggml.so")
    link.type, link.linkname = tarfile.SYMTYPE, "libggml.so.0"
    archive = _tar(
        tmp_path / "a.tar.gz",
        [
            (top, None),
            _file("llama-b1/llama-server", b"ELF", 0o755),
            _file("llama-b1/libggml.so.0", b"so"),
            (link, None),
        ],
    )
    extract(archive, tmp_path / "out")
    server = tmp_path / "out" / "llama-server"
    assert server.read_bytes() == b"ELF"
    assert (tmp_path / "out" / "libggml.so").is_symlink()
    if sys.platform != "win32":
        assert server.stat().st_mode & 0o111  # still executable


def test_a_member_outside_the_target_is_refused(tmp_path):
    archive = _tar(tmp_path / "evil.tar.gz", [_file("../evil.txt", b"x")])
    with pytest.raises(tarfile.FilterError):
        extract(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()
```

Run: `uv run pytest tests/test_fetch_assets.py -q` → FAIL (`cannot import name 'extract'`). (On Windows the symlink assertion needs symlink rights; if `extract` fails there with `OSError` for the link, mark that test `@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")`.)

- [ ] **Step 3: `fetch_assets.py`**

```python
"""Download pinned dev/build assets. Usage: uv run python scripts/fetch_assets.py [name ...]

"llama" means this platform's llama.cpp build; no names means every asset for this platform.
"""

import json
import sys
import tarfile
import zipfile
from pathlib import Path

import truststore

from tamra.download import download_verified

ROOT = Path(__file__).resolve().parents[1]
LLAMA = {"win32": "llama-windows", "linux": "llama-linux"}


def select(assets: dict, names: list[str], platform: str) -> list[str]:
    """The asset names to fetch on this platform; KeyError for an unknown name."""
    if not names:
        return [n for n, spec in assets.items() if spec.get("platform", platform) == platform]
    chosen = [LLAMA.get(platform, "llama-linux") if n == "llama" else n for n in names]
    for name in chosen:
        if name not in assets:
            raise KeyError(name)
    return chosen


def extract(archive: Path, target: Path) -> None:
    """Unpack a .zip or .tar.gz into target. A tar whose entries all sit in one top-level folder
    (llama.cpp's Linux builds) is unpacked without it. tarfile's "data" filter keeps the
    executable bits and the .so symlinks, and refuses absolute paths and paths outside target."""
    if not archive.name.endswith((".tar.gz", ".tgz")):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
        return
    with tarfile.open(archive) as tf:
        members = tf.getmembers()
        tops = {m.name.split("/", 1)[0] for m in members}
        top = tops.pop() if len(tops) == 1 else None
        if top is not None and any(m.name == top and m.isdir() for m in members):
            members = [
                m.replace(name=m.name[len(top) + 1 :], deep=False)
                for m in members
                if m.name.startswith(top + "/")
            ]
        target.mkdir(parents=True, exist_ok=True)
        tf.extractall(target, members=members, filter="data")


def main(names: list[str]) -> int:
    truststore.inject_into_ssl()
    assets = json.loads((ROOT / "scripts" / "assets.json").read_text(encoding="utf-8"))
    try:
        chosen = select(assets, names, sys.platform)
    except KeyError as e:
        print(f"unknown asset: {e.args[0]}; known: llama, {', '.join(assets)}")
        return 2
    for name in chosen:
        spec = assets[name]
        dest = ROOT / spec["dest"]
        print(f"{name}: {dest}")
        download_verified(spec["url"], dest, spec["sha256"])
        if "extract_to" in spec:
            extract(dest, ROOT / spec["extract_to"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests and the real fetch**

Run (Windows): `uv run pytest tests/test_fetch_assets.py -q` → PASS; `uv run python scripts/fetch_assets.py llama` → `llama-windows: ...` and `vendor\llama\llama-server.exe` still runs (`vendor\llama\llama-server.exe --version`).
Run (WSL): `tamra-sync && cd ~/tamra-dev && uv run pytest tests/test_fetch_assets.py -q && uv run python scripts/fetch_assets.py llama && vendor/llama/llama-server --version && vendor/llama/llama-server --list-devices`
Expected: the version prints; the device list may be empty in WSL (CPU only).
Run (WSL, real models): `uv run pytest -m assets -q`
Expected: PASS, except tests that require a GPU (`test_detect_with_the_real_llama_server` asserts a GPU is listed; skip it in WSL with `-k "not test_detect_with_the_real_llama_server"` if `--list-devices` printed none, and say so in the task report).

- [ ] **Step 5: Commit**

```bash
uv run ruff check && uv run ruff format
git add scripts/assets.json scripts/fetch_assets.py tests/test_fetch_assets.py
git commit -m "feat: pin llama.cpp's Ubuntu Vulkan build and fetch the one for this platform"
```

---

### Task 9: The Linux PyInstaller bundle and a cross-platform smoke test

**Files:**
- Modify: `packaging/tamra.spec`
- Create: `scripts/build.sh` (executable: `git update-index --chmod=+x scripts/build.sh`)
- Modify: `scripts/exe_smoke.py`

- [ ] **Step 1: `packaging/tamra.spec`**

```python
# PyInstaller spec — build with: uv run pyinstaller packaging/tamra.spec --noconfirm
# Windows: dist/Tamra/Tamra.exe. Linux: dist/tamra/tamra (scripts/make_deb.py packages it).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"
NAME = "Tamra" if WINDOWS else "tamra"
ICON = ROOT / "packaging" / ("tamra.ico" if WINDOWS else "tamra.png")

hiddenimports = (
    collect_submodules("uvicorn")
    # PyInstaller's hook-keyring already covers these; kept as belt-and-braces for the OS
    # credential store backend.
    + collect_submodules("keyring.backends")
    + ["anthropic", "truststore"]
    + ["tamra.app", "tamra.selfcheck", "tamra.embedder", "tamra.mcp_server"]
)
excludes = []
if WINDOWS:
    hiddenimports += ["keyring.backends.Windows"]
else:
    hiddenimports += [
        "keyring.backends.SecretService",
        "secretstorage",
        "jeepney",
        "webview.platforms.qt",  # pywebview imports its backend by name at runtime
        "qtpy",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        "PySide6.QtNetwork",
    ]
    # Other bindings and pywebview backends, never used on Linux.
    excludes += ["PyQt5", "PyQt6", "PySide2", "gi", "webview.platforms.gtk", "webview.platforms.cef"]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=collect_dynamic_libs("sqlite_vec"),
    datas=[
        (str(ROOT / "ui" / "dist"), "ui/dist"),
        (str(ROOT / "vendor" / "llama"), "vendor/llama"),
        (str(ROOT / "src" / "tamra" / "models" / "catalog.json"), "tamra/models"),
        (str(ICON), "packaging"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "NOTICE"), "."),
    ],
    hiddenimports=hiddenimports,
    excludes=excludes,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=str(ICON) if WINDOWS else None,  # Linux takes the icon from the .desktop entry
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
```

Note: PyInstaller puts `vendor/llama` under `datas`, which keeps the files but may not keep the executable bit or the `.so` symlinks. Step 4 checks it; if `llama-server` is not executable or a `libggml*.so` link is a copy that breaks loading, move `vendor/llama` from `datas` to `binaries` on Linux (`[(str(p), "vendor/llama") for p in (ROOT / "vendor" / "llama").iterdir()]`) and re-check.

- [ ] **Step 2: `scripts/build.sh`**

```bash
#!/usr/bin/env bash
# Builds dist/tamra/tamra; with --deb, also dist/tamra_<version>_amd64.deb.
# The Linux version of scripts/build.ps1 [-Installer]. Run on Ubuntu 24.04.
set -euo pipefail
cd "$(dirname "$0")/.."
deb=0
case "${1:-}" in
  --deb) deb=1 ;;
  "") ;;
  *) echo "usage: scripts/build.sh [--deb]" >&2; exit 2 ;;
esac
npm --prefix ui ci
npm --prefix ui run build
uv run python scripts/fetch_assets.py llama
uv run pyinstaller packaging/tamra.spec --noconfirm --distpath dist --workpath build
if [ "$deb" = 1 ]; then
  uv run python scripts/make_deb.py
fi
```

(`scripts/make_deb.py` arrives in Task 10; until then only run without `--deb`.)

- [ ] **Step 3: `exe_smoke.py` on both platforms.** Add `import sys`. Replace `llama_server_pids` with:

```python
def llama_server_pids() -> set[int]:
    """PIDs of running llama-server processes (read only: nothing is stopped by name)."""
    if sys.platform != "win32":
        found = subprocess.run(["pgrep", "-x", "llama-server"], capture_output=True, text=True)
        return {int(pid) for pid in found.stdout.split()}  # pgrep exits 1 when there is none
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
        ...  # the rest of the existing Windows code, unchanged
```

The `api_mode_step` argument `ROOT / "vendor" / "llama" / "llama-server.exe"` becomes `llama_server()` (`from tamra.paths import llama_server`). The default exe and its message:

```python
DEFAULT_EXE = ROOT / "dist" / ("Tamra/Tamra.exe" if sys.platform == "win32" else "tamra/tamra")
BUILD = "./scripts/build.ps1" if sys.platform == "win32" else "./scripts/build.sh"
...
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
...
        raise SystemExit(f"missing {args.exe}; build it with {BUILD}")
```

Docstring: "dist/Tamra/Tamra.exe" → "the packaged app (dist/Tamra/Tamra.exe, or dist/tamra/tamra on Linux)", and "llama-server.exe" → "llama-server" where it describes the process.

- [ ] **Step 4: Build and check in WSL**

Run (WSL): `tamra-sync && cd ~/tamra-dev && chmod +x scripts/build.sh && ./scripts/build.sh`
Expected: `dist/tamra/tamra` exists. Then:

```bash
test -x dist/tamra/_internal/vendor/llama/llama-server && echo exec-ok
ls -l dist/tamra/_internal/vendor/llama/libggml.so      # a symlink, or a working copy
find dist/tamra -name QtWebEngineProcess                 # note the path for Task 10's AppArmor profile
du -sh dist/tamra                                        # note the size for the PR
dist/tamra/tamra selfcheck --report /tmp/s.json; cat /tmp/s.json
xvfb-run -a dist/tamra/tamra selfcheck --window --report /tmp/w.json; cat /tmp/w.json
```

Expected: `exec-ok`; selfcheck `"ok": true` with `"keyring_backend"` a Secret Service or fail backend (WSL may have no keyring daemon: if `providers.ok` is false only because of `keyring.backends.fail`, note it and run `gnome-keyring` checks on the real desktop, Task 10 manual list); window `"ok": true`. `QtWebEngineProcess` is expected at `dist/tamra/_internal/PySide6/Qt/libexec/QtWebEngineProcess`.

Run (WSL, end to end): `uv run python scripts/exe_smoke.py --out /tmp/smoke.json` (needs `.models` from Task 0)
Expected: the report's `problems` list is empty. A GPU-only expectation may fail in WSL; record what failed and why.

Run (Windows): `./scripts/build.ps1` and `uv run python scripts/exe_smoke.py` still pass (the spec branch must not change the Windows bundle).

- [ ] **Step 5: Commit**

```bash
uv run ruff check && uv run ruff format
git add packaging/tamra.spec scripts/build.sh scripts/exe_smoke.py
git update-index --chmod=+x scripts/build.sh
git commit -m "feat: build the Linux bundle dist/tamra with PyInstaller; the smoke test runs on Linux"
```

---

### Task 10: The `.deb`

**Files:**
- Create: `scripts/make_deb.py`
- Create: `packaging/linux/tamra.desktop`, `packaging/linux/apparmor-tamra`, `packaging/linux/postinst`, `packaging/linux/postrm`, `packaging/linux/copyright-header.txt`, `packaging/linux/build-packages.txt`
- Modify: `scripts/build.sh` (nothing: it already calls `make_deb.py`)
- Test: `tests/test_make_deb.py` (new)

**Interfaces:**
- Produces: `make_deb.control(version: str, depends: list[str], installed_kb: int) -> str`, `make_deb.depends(bundle: Path) -> list[str]`, `make_deb.main() -> int`; output `dist/tamra_<version>_amd64.deb`.

- [ ] **Step 1: `packaging/linux/tamra.desktop`**

```ini
[Desktop Entry]
Type=Application
Name=Tamra
GenericName=Document Q&A
GenericName[th]=ถามตอบจากเอกสาร
Comment=Ask questions about your own documents, with citations you can check
Comment[th]=ถามคำถามจากเอกสารของคุณเอง พร้อมแหล่งอ้างอิงที่ตรวจสอบได้
Exec=tamra
Icon=tamra
Terminal=false
Categories=Office;
Keywords=documents;search;citations;AI;
StartupWMClass=tamra
```

- [ ] **Step 2: `packaging/linux/apparmor-tamra`** (path from Task 9 Step 4)

```
# Lets Tamra's web engine (Qt WebEngine, Chromium) create the user namespaces its sandbox needs.
# Ubuntu 23.10 and later allow them only to programs with a profile like this, as for Chrome.
abi <abi/4.0>,
include <tunables/global>

profile tamra /opt/tamra/{tamra,_internal/PySide6/Qt/libexec/QtWebEngineProcess} flags=(unconfined) {
  userns,

  include if exists <local/tamra>
}
```

- [ ] **Step 3: `postinst` and `postrm`** (`packaging/linux/postinst`):

```sh
#!/bin/sh
# Load the profile that lets Tamra's web engine use its sandbox, where AppArmor is on.
set -e
if [ "$1" = "configure" ] && [ -d /sys/kernel/security/apparmor ] \
    && command -v apparmor_parser >/dev/null 2>&1; then
    apparmor_parser -r -T -W /etc/apparmor.d/tamra || true
fi
exit 0
```

`packaging/linux/postrm`:

```sh
#!/bin/sh
# Unload the profile when Tamra is removed. The data in ~/.local/share/tamra stays.
set -e
if [ "$1" = "remove" ] && [ -d /sys/kernel/security/apparmor ] \
    && command -v apparmor_parser >/dev/null 2>&1; then
    apparmor_parser -R /etc/apparmor.d/tamra 2>/dev/null || true
fi
exit 0
```

- [ ] **Step 4: `packaging/linux/copyright-header.txt`**

```
Tamra
Source: https://github.com/EARTH157/tamra

Tamra is licensed under the Apache License, Version 2.0, below, with the notices in NOTICE.

This package also contains Qt and PySide6 (Qt for Python), licensed under the GNU Lesser General
Public License version 3 (LGPL-3.0-only); their license files are in
/usr/share/doc/tamra/licenses/. They are shared libraries in /opt/tamra/_internal/PySide6 and can
be replaced with compatible builds. Their source code: https://code.qt.io/.
Qt WebEngine includes Chromium, under BSD-style licenses (see the Qt license files).
llama.cpp (MIT) is in /opt/tamra/_internal/vendor/llama, with its LICENSE file.
```

- [ ] **Step 5: `packaging/linux/build-packages.txt`** (one package per line: what CI and a developer install on Ubuntu 24.04 to build and test the `.deb`; Step 8 may add lines when `make_deb.py` names a missing library):

```
libgomp1
libvulkan1
libnss3
libxkbcommon-x11-0
libxcb-cursor0
libxcb-icccm4
libxcb-image0
libxcb-keysyms1
libxcb-render-util0
libxcb-shape0
libxcb-xinerama0
libxcb-xkb1
libasound2t64
libxkbfile1
libegl1
libgl1
libfontconfig1
libdbus-1-3
xvfb
fonts-tlwg-garuda-ttf
fonts-wqy-microhei
```

- [ ] **Step 6: Failing tests** in `tests/test_make_deb.py`:

```python
"""scripts/make_deb.py: the control file and the package layout (spec §4.3)."""

import sys

import pytest
from make_deb import LINUX, WEBENGINE_PROCESS, control


def test_the_control_file_names_the_package_and_its_dependencies():
    text = control("0.2.0", ["libc6", "libnss3"], 123456)
    fields = dict(line.split(": ", 1) for line in text.splitlines() if ": " in line and not line.startswith(" "))
    assert fields["Package"] == "tamra"
    assert fields["Version"] == "0.2.0"
    assert fields["Architecture"] == "amd64"
    assert fields["Depends"] == "libc6, libnss3"
    assert fields["Installed-Size"] == "123456"
    assert "gnome-keyring" in fields["Recommends"]
    assert text.endswith("\n")


def test_the_apparmor_profile_names_the_web_engine_process():
    profile = (LINUX / "apparmor-tamra").read_text(encoding="utf-8")
    assert "userns," in profile
    assert WEBENGINE_PROCESS in profile  # "_internal/PySide6/Qt/libexec/QtWebEngineProcess"


@pytest.mark.skipif(sys.platform == "win32", reason="the .deb is built on Linux")
def test_the_maintainer_scripts_are_posix_sh():
    for name in ("postinst", "postrm"):
        assert (LINUX / name).read_text(encoding="utf-8").startswith("#!/bin/sh\n")
```

Run: `uv run pytest tests/test_make_deb.py -q` → FAIL (`No module named 'make_deb'`).

- [ ] **Step 7: `scripts/make_deb.py`**

```python
"""Build dist/tamra_<version>_amd64.deb from the PyInstaller folder dist/tamra.

Run on Ubuntu 24.04 after PyInstaller: uv run python scripts/make_deb.py (scripts/build.sh --deb
runs both). Needs dpkg-deb, readelf and ldconfig, and the system libraries the bundle loads
(packaging/linux/build-packages.txt), so their packages can be named in Depends.
docs/superpowers/specs/2026-10-26-ubuntu-support-design.md §4.3 lists what the package holds.
"""

import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "dist" / "tamra"
LINUX = ROOT / "packaging" / "linux"
STAGE = ROOT / "build" / "deb"
WEBENGINE_PROCESS = "_internal/PySide6/Qt/libexec/QtWebEngineProcess"

MAINTAINER = "EARTH157 <EARTH157@users.noreply.github.com>"
# Not found by scanning the binaries: certificates for truststore, xdg-open for "Open file".
EXTRA_DEPENDS = ["ca-certificates", "xdg-utils"]
RECOMMENDS = ["gnome-keyring", "zenity", "mesa-vulkan-drivers", "fonts-thai-tlwg", "fonts-noto-cjk"]
SUMMARY = "Ask questions about your own documents, with checkable citations"
DESCRIPTION = """\
 Tamra answers questions about your documents (PDF, DOCX, TXT, MD) in Thai,
 English and Chinese. Every answer cites its sources, and any sentence can be
 checked against the exact passage. It runs a local model through llama.cpp,
 or a cloud API, and works offline once the models are downloaded."""
# Distributions whose license files ship with the package (LGPL-3.0: Qt and PySide6).
LICENSED = ("PySide6_Essentials", "PySide6_Addons", "shiboken6")


def control(version: str, depends: list[str], installed_kb: int) -> str:
    return "\n".join(
        [
            "Package: tamra",
            f"Version: {version}",
            "Section: utils",
            "Priority: optional",
            "Architecture: amd64",
            f"Maintainer: {MAINTAINER}",
            f"Installed-Size: {installed_kb}",
            f"Depends: {', '.join(depends)}",
            f"Recommends: {', '.join(RECOMMENDS)}",
            "Homepage: https://github.com/EARTH157/tamra",
            f"Description: {SUMMARY}",
            DESCRIPTION,
        ]
    ) + "\n"


def elf_files(folder: Path) -> list[Path]:
    """Every ELF file in the bundle: executables, .so files and Qt plugins."""
    found = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as f:
                if f.read(4) == b"\x7fELF":
                    found.append(path)
    return found


_NEEDED = re.compile(r"\(NEEDED\)\s+Shared library: \[(.+?)\]")
_LDCONFIG = re.compile(r"^\s*(\S+) \(libc6,x86-64[^)]*\) => (\S+)$")


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def depends(bundle: Path) -> list[str]:
    """The packages that provide every library the bundle needs and does not carry itself."""
    inside = {p.name for p in bundle.rglob("*") if ".so" in p.name}
    wanted: set[str] = set()
    for elf in elf_files(bundle):
        wanted |= set(_NEEDED.findall(_run(["readelf", "-d", str(elf)])))
    wanted -= inside
    where: dict[str, Path] = {}
    for line in _run(["ldconfig", "-p"]).splitlines():
        m = _LDCONFIG.match(line)
        if m and m[1] in wanted:
            where.setdefault(m[1], Path(m[2]))
    missing = sorted(wanted - where.keys())
    if missing:
        raise SystemExit(
            "not on this system: " + ", ".join(missing) + ". Install their packages "
            "(packaging/linux/build-packages.txt) so Depends can name them."
        )
    return sorted({_package_of(path) for path in where.values()} | set(EXTRA_DEPENDS))


def _package_of(path: Path) -> str:
    """The package that owns a library file. With merged /usr, dpkg may know it under /lib or
    under /usr/lib, so both spellings are asked."""
    spellings = {path, path.resolve()}
    for p in list(spellings):
        s = str(p)
        spellings.add(Path(s[4:]) if s.startswith("/usr/") else Path("/usr" + s))
    for p in sorted(spellings):
        done = subprocess.run(["dpkg", "-S", str(p)], capture_output=True, text=True)
        if done.returncode == 0:
            owner = done.stdout.splitlines()[0].rsplit(": ", 1)[0]  # "libnss3:amd64: /usr/lib/..."
            return owner.split(",")[0].strip().split(":")[0]
    raise SystemExit(f"no package owns {path}")


def _install(source: Path, dest: Path, mode: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    dest.chmod(mode)


def _license_files(dest: Path) -> None:
    for name in LICENSED:
        files = [
            f for f in importlib.metadata.files(name) or [] if "LICENSE" in f.name.upper()
        ]
        if not files:
            raise SystemExit(f"no license file found for {name}")
        for f in files:
            _install(Path(f.locate()), dest / name / f.name, 0o644)


def stage(version: str) -> Path:
    if not (BUNDLE / WEBENGINE_PROCESS).is_file():
        raise SystemExit(f"missing {BUNDLE / WEBENGINE_PROCESS}; the AppArmor profile names it")
    shutil.rmtree(STAGE, ignore_errors=True)
    shutil.copytree(BUNDLE, STAGE / "opt" / "tamra", symlinks=True)
    for path in (STAGE / "opt").rglob("*"):  # no group or other write bits
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o022)
    (STAGE / "usr" / "bin").mkdir(parents=True)
    (STAGE / "usr" / "bin" / "tamra").symlink_to("/opt/tamra/tamra")
    share = STAGE / "usr" / "share"
    _install(LINUX / "tamra.desktop", share / "applications" / "tamra.desktop", 0o644)
    _install(ROOT / "ui" / "src" / "logo.svg", share / "icons/hicolor/scalable/apps/tamra.svg", 0o644)
    _install(ROOT / "packaging" / "tamra.png", share / "icons/hicolor/256x256/apps/tamra.png", 0o644)
    _install(LINUX / "apparmor-tamra", STAGE / "etc" / "apparmor.d" / "tamra", 0o644)
    doc = share / "doc" / "tamra"
    doc.mkdir(parents=True)
    parts = [LINUX / "copyright-header.txt", ROOT / "NOTICE", ROOT / "LICENSE"]
    (doc / "copyright").write_text(
        "\n\n".join(p.read_text(encoding="utf-8").strip() for p in parts) + "\n", encoding="utf-8"
    )
    _license_files(doc / "licenses")
    debian = STAGE / "DEBIAN"
    debian.mkdir()
    for script in ("postinst", "postrm"):
        _install(LINUX / script, debian / script, 0o755)
    (debian / "conffiles").write_text("/etc/apparmor.d/tamra\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in STAGE.rglob("*") if p.is_file() and not p.is_symlink())
    (debian / "control").write_text(
        control(version, depends(BUNDLE), size // 1024 + 1), encoding="utf-8"
    )
    return STAGE


def main() -> int:
    if sys.platform != "linux":
        raise SystemExit("build the .deb on Ubuntu")
    if not (BUNDLE / "tamra").is_file():
        raise SystemExit(f"missing {BUNDLE / 'tamra'}; run scripts/build.sh first")
    sys.path.insert(0, str(ROOT / "src"))
    import tamra

    stage(tamra.__version__)
    out = ROOT / "dist" / f"tamra_{tamra.__version__}_amd64.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(STAGE), str(out)], check=True)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Run the tests, build, install, check, remove** (WSL)

```bash
tamra-sync && cd ~/tamra-dev && uv run pytest tests/test_make_deb.py -q && ./scripts/build.sh --deb
```

Expected: PASS, then `wrote .../dist/tamra_0.1.2_amd64.deb (N MB)`. If `make_deb.py` stops with "not on this system: X", find the package (`apt-file` is not installed; use `dpkg -S` on a 24.04 system that has it, or search packages.ubuntu.com for the soname), add it to `build-packages.txt`, install it as root, and run again. Then, as root and as the user:

```bash
dpkg-deb --info dist/tamra_*_amd64.deb      # look over Depends and Installed-Size
dpkg-deb --contents dist/tamra_*_amd64.deb | grep -E "usr/bin/tamra|tamra.desktop|apparmor.d/tamra|hicolor"
# root (wsl -u root):
apt-get install -y /home/<user>/tamra-dev/dist/tamra_0.1.2_amd64.deb
# user:
tamra selfcheck --report /tmp/s.json && cat /tmp/s.json
xvfb-run -a tamra selfcheck --window --report /tmp/w.json && cat /tmp/w.json
desktop-file-validate /usr/share/applications/tamra.desktop || true   # only if installed
tamra &   # WSLg: the window opens on the Windows desktop; close it
# root:
apt-get remove -y tamra && test ! -e /opt/tamra && echo removed
```

Expected: both selfchecks `"ok": true` (see Task 9 Step 4 for the keyring note), the window opens, `removed`. The user's data folder is untouched by `apt remove`.

- [ ] **Step 9: Commit**

```bash
uv run ruff check && uv run ruff format
git add scripts/make_deb.py packaging/linux tests/test_make_deb.py
git commit -m "feat: package the Linux bundle as tamra_<version>_amd64.deb with a menu entry and an AppArmor profile"
```

---

### Task 11: CI and release on Ubuntu

**Files:** `.github/workflows/ci.yml`, `.github/workflows/release.yml`

- [ ] **Step 1: `ci.yml` test matrix.** Replace the `test` job's `runs-on: windows-latest` with a matrix and add the Linux packages step before `setup-uv`:

```yaml
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [windows-latest, ubuntu-24.04]
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v5
      - name: Fonts and Qt libraries the tests use
        if: runner.os == 'Linux'
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends $(cat packaging/linux/build-packages.txt)
      - uses: astral-sh/setup-uv@v6
      ...  # the existing steps, unchanged
```

- [ ] **Step 2: `ci.yml` Ubuntu package job** (the Windows `package` job stays as it is):

```yaml
  package-ubuntu:
    needs: test
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v5
      - name: Build tools and the libraries the .deb depends on
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends $(cat packaging/linux/build-packages.txt)
      - uses: astral-sh/setup-uv@v6
      - uses: actions/setup-node@v5
        with:
          node-version: 24
          cache: npm
          cache-dependency-path: ui/package-lock.json
      - run: uv sync --locked
      - run: ./scripts/build.sh --deb
      - name: Install the .deb
        run: sudo apt-get install -y ./dist/tamra_*_amd64.deb
      - name: Selfcheck the installed app
        run: |
          test -x /opt/tamra/_internal/vendor/llama/llama-server
          tamra selfcheck --report selfcheck.json || { cat selfcheck.json; exit 1; }
          cat selfcheck.json
          # The runner restricts user namespaces through AppArmor, as Ubuntu Desktop does,
          # so this also checks the profile the package installs.
          xvfb-run -a tamra selfcheck --window --report window.json || { cat window.json; exit 1; }
          cat window.json
      - uses: actions/upload-artifact@v4
        with:
          name: Tamra-ubuntu
          path: dist/tamra_*_amd64.deb
          if-no-files-found: error
```

If the runner has no Secret Service, `providers.ok` is false (keyring's fail backend) and the first selfcheck exits 1. Then start one for the step: prefix the two selfcheck lines with `dbus-run-session -- sh -c 'echo -n "" | gnome-keyring-daemon --unlock --components=secrets && ...'` and add `gnome-keyring dbus-x11` to the install line of this job only. Decide from the first CI run's output, not in advance.

- [ ] **Step 3: `release.yml`.** Add `"scripts/build.sh"`, `"scripts/make_deb.py"` to the `pull_request.paths`. Add a `deb` job (on `ubuntu-24.04`) with the steps of `package-ubuntu` plus, before the build, the version and tag check (as in `installer`, in bash):

```yaml
      - name: Read the version
        id: version
        run: echo "version=$(uv run python -c 'import tamra; print(tamra.__version__)')" >> "$GITHUB_OUTPUT"
      - name: The tag matches the version
        if: startsWith(github.ref, 'refs/tags/')
        env:
          TAG: ${{ github.ref_name }}
          VERSION: ${{ steps.version.outputs.version }}
        run: |
          if [ "$TAG" != "v$VERSION" ]; then echo "Tag $TAG does not match tamra.__version__ $VERSION"; exit 1; fi
```

and, after the selfchecks:

```yaml
      - name: Remove the package
        run: |
          sudo apt-get remove -y tamra
          test ! -e /opt/tamra
      - name: Write the sha256
        env:
          VERSION: ${{ steps.version.outputs.version }}
        run: |
          cd dist && sha256sum "tamra_${VERSION}_amd64.deb" > "tamra_${VERSION}_amd64.deb.sha256"
          cat "tamra_${VERSION}_amd64.deb.sha256"
      - uses: actions/upload-artifact@v4
        with:
          name: Tamra-deb
          path: |
            dist/tamra_${{ steps.version.outputs.version }}_amd64.deb
            dist/tamra_${{ steps.version.outputs.version }}_amd64.deb.sha256
          if-no-files-found: error
```

The `release` job: `needs: [installer, deb]`, a second `download-artifact` of `Tamra-deb` into `dist`, and `gh release create` lists `"dist/tamra_$env:VERSION`_amd64.deb" "dist/tamra_$env:VERSION`_amd64.deb.sha256"` after the Windows files (PowerShell: escape `_` after a variable with a backtick, or use `"dist/tamra_$($env:VERSION)_amd64.deb"`; use the `$()` form).

- [ ] **Step 4: Check the YAML.** Nothing here runs GitHub Actions locally, and pushing is the user's call. Check by eye that each new step's `if:`, `env:` and `shell:` follow the existing jobs, that `release` downloads both artifacts, and that `pull_request.paths` covers the new scripts. The first CI run after the user pushes is the real check.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/release.yml
git commit -m "ci: test on Ubuntu 24.04, build and check the .deb, attach it to releases"
```

---

### Task 12: Documentation

**Files:** `README.md`, `CLAUDE.md`, `NOTICE`, `docs/superpowers/specs/2026-10-02-tamra-design.md`, `docs/superpowers/specs/2026-10-26-ubuntu-support-design.md`, `pyproject.toml` (`description`)

- [ ] **Step 1: README (English).** In "Install", add an "Ubuntu 24.04 or later" subsection after the Windows steps:

```markdown
### Ubuntu

You need Ubuntu 24.04 or later, 64-bit (x86_64).

1. Download `tamra_<version>_amd64.deb` from the [latest release](https://github.com/EARTH157/tamra/releases/latest).
2. Install it: `sudo apt install ./tamra_<version>_amd64.deb`. apt also installs the libraries
   Tamra needs. Tamra appears in the app menu.
3. Your data is in `~/.local/share/tamra` (or `$XDG_DATA_HOME/tamra`). API keys are kept in
   GNOME Keyring.
4. Uninstall with `sudo apt remove tamra`. This keeps your data; delete
   `~/.local/share/tamra` to remove it too.

If the window stays blank in a virtual machine, start Tamra with
`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu tamra`.
```

In "From source", add after the Windows requirements: "On Ubuntu 24.04 you also need the libraries in `packaging/linux/build-packages.txt` (`sudo apt install $(cat packaging/linux/build-packages.txt)`). On Ubuntu 23.10 and later a run from source has no AppArmor profile, so start it with `QTWEBENGINE_DISABLE_SANDBOX=1 uv run tamra`. Build the `.deb` with `./scripts/build.sh --deb`." Everywhere the README says Tamra is a Windows app, say "Windows and Ubuntu". The "Where the key is stored" paragraph names GNOME Keyring on Ubuntu.

- [ ] **Step 2: README (Thai section)** — the same facts in Thai:

```markdown
### Ubuntu

ต้องใช้ Ubuntu 24.04 ขึ้นไป แบบ 64 บิต (x86_64)

1. ดาวน์โหลด `tamra_<version>_amd64.deb` จาก[รีลีสล่าสุด](https://github.com/EARTH157/tamra/releases/latest)
2. ติดตั้งด้วย `sudo apt install ./tamra_<version>_amd64.deb` apt จะติดตั้งไลบรารีที่ Tamra ต้องใช้ให้ด้วย แล้ว Tamra จะอยู่ในเมนูแอป
3. ข้อมูลของคุณอยู่ที่ `~/.local/share/tamra` (หรือ `$XDG_DATA_HOME/tamra`) API key เก็บไว้ใน GNOME Keyring
4. ถอนการติดตั้งด้วย `sudo apt remove tamra` ข้อมูลจะยังอยู่ ถ้าต้องการลบด้วย ให้ลบโฟลเดอร์ `~/.local/share/tamra`
```

- [ ] **Step 3: CLAUDE.md.** Commands: add `./scripts/build.sh --deb                  # Ubuntu: UI build + PyInstaller → dist/tamra/tamra and dist/tamra_<version>_amd64.deb`. Rules: Local LLM rule → "llama.cpp's prebuilt `llama-server` (`llama-server.exe` on Windows; Vulkan build)"; Licenses rule → append "PySide6/Qt (LGPLv3) is allowed for the Linux window; never PyQt (GPL)."; Data rule → "user data lives under `%LOCALAPPDATA%\Tamra\` on Windows and `$XDG_DATA_HOME/tamra` (`~/.local/share/tamra`) on Linux (override: `TAMRA_DATA_DIR`)." Project line: "an open-source (Apache-2.0) desktop app for Windows and Ubuntu". Architecture: "A pywebview window (WebView2 on Windows, Qt WebEngine on Linux) loads ...". Add `childproc` and `desktop` to the module list: "`childproc`: starts child processes that end with Tamra." and "`desktop`: one instance per data folder, opening files, the error dialog, and the window's platform options."

- [ ] **Step 4: NOTICE.** Add a paragraph: "The Linux package (tamra_<version>_amd64.deb) also contains Qt and PySide6 (Qt for Python), licensed under the GNU Lesser General Public License v3 (LGPL-3.0-only), https://www.qt.io/; Qt WebEngine includes Chromium (BSD-style licenses)."

- [ ] **Step 5: Specs.** Main spec: the intro "a Windows desktop app" → "a desktop app for Windows and Ubuntu"; §1 non-goal → "Platforms other than Windows 10/11 x64 and Ubuntu 24.04+ x86_64 (docs/superpowers/specs/2026-10-26-ubuntu-support-design.md)."; §2 Stack row → "pywebview (WebView2 on Windows, Qt WebEngine on Linux) window"; Data location row → add "; `~/.local/share/tamra` on Linux"; §11 → a last bullet "Linux: a `.deb` for Ubuntu 24.04+, see docs/superpowers/specs/2026-10-26-ubuntu-support-design.md §4–5." Ubuntu spec status → "Status: implemented (plan docs/superpowers/plans/2026-10-26-ubuntu-support.md)." `pyproject.toml` description → "Offline-first desktop app (Windows, Ubuntu) for asking questions about your documents, with verifiable citations."

- [ ] **Step 6: Full check on both platforms and commit**

Run (Windows): `uv run ruff check; uv run ruff format --check; uv run pytest -q; npm --prefix ui test` → all pass.
Run (WSL): `tamra-sync && cd ~/tamra-dev && uv run pytest -q` → all pass.

```bash
git add README.md CLAUDE.md NOTICE docs pyproject.toml
git commit -m "docs: install and build on Ubuntu"
```

---

## Manual check before the first release with the `.deb` (spec §6)

Not automatable here (WSL has no AppArmor userns restriction and no GNOME session). On a real Ubuntu 24.04 desktop (GNOME, Wayland, AppArmor on): install with apt; the menu entry opens the window with Tamra's icon in the dock; the folder picker adds a project; a PDF is answered with citations from a local model (GPU via Vulkan, or CPU); Check source opens the source panel; opening the file uses the default app; an API key saved in GNOME Keyring survives a restart; a second launch says "already running"; closing Tamra ends llama-server, and so does `kill -9` of Tamra; `apt remove` keeps `~/.local/share/tamra`.
