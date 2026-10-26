"""What Tamra asks of the desktop: one Tamra per data folder, opening a file in its default app,
an error dialog, and how to start the window. Windows and Linux do each differently."""

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


def release_instance(handle) -> None:
    if sys.platform == "win32":
        _kernel32().CloseHandle(handle)
    else:
        os.close(handle)


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _windows_mutex(data_dir: Path):
    digest = hashlib.sha1(str(data_dir.resolve()).lower().encode("utf-8")).hexdigest()[:12]
    ctypes.set_last_error(0)
    handle = _kernel32().CreateMutexW(None, 0, f"Local\\Tamra-{digest}")
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
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
    """An error dialog: the windowed app has no console to print to. On Linux zenity (part of
    Ubuntu Desktop, and the .deb recommends it), else stderr."""
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "Tamra", 0x10)  # MB_ICONERROR
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
