"""Desktop entry: start Core and the API on 127.0.0.1, then show the window (or dev mode)."""

import ctypes
import logging
import secrets
import threading
import time

import httpx
import uvicorn

from tamra.core import Core
from tamra.logs import setup_logging
from tamra.net import free_port
from tamra.paths import data_dir, models_dir, resource_dir
from tamra.server import create_app

log = logging.getLogger(__name__)

DEV_PORT = 8765
DEV_TOKEN = "dev"


def build_core() -> Core:
    """Core with data in data_dir(), models in models_dir(), and the bundled llama-server."""
    return Core(data_dir(), models_dir(), resource_dir() / "vendor" / "llama" / "llama-server.exe")


class FolderPicker:
    """The Windows folder picker on the app window. The API calls it from a worker thread."""

    def __init__(self) -> None:
        self.window = None  # set once the window exists

    def __call__(self) -> str | None:
        if self.window is None:
            return None
        import webview

        chosen = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        return str(chosen[0]) if chosen else None


def _wait_until_up(
    port: int, token: str, thread: threading.Thread | None = None, timeout: float = 15.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if thread is not None and not thread.is_alive():
            raise RuntimeError(
                f"Tamra core stopped while starting (is port {port} already in use?)"
            )
        try:
            r = httpx.get(
                f"http://127.0.0.1:{port}/api/health",
                headers={"X-Tamra-Token": token},
                trust_env=False,
            )
            if r.status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Tamra core did not start")


def run(dev: bool = False) -> None:
    setup_logging(data_dir() / "logs", console=dev)
    port, token = (DEV_PORT, DEV_TOKEN) if dev else (free_port(), secrets.token_urlsafe(32))
    ui_dir = None if dev else resource_dir() / "ui" / "dist"
    picker = None if dev else FolderPicker()
    core: Core | None = None
    failure: Exception | None = None
    try:
        core = build_core()
        core.start()
        config = uvicorn.Config(
            create_app(token, ui_dir, core, pick_folder=picker),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            log_config=None,
            timeout_graceful_shutdown=3,  # an open SSE stream must not hold shutdown
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="tamra-api", daemon=True)
        thread.start()
        try:
            _wait_until_up(port, token, thread)
            if picker is None:
                _wait_in_dev_mode(port, token, thread)
            else:
                _show_window(port, token, picker)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
    except Exception as e:
        log.exception("Tamra stopped because of an error")
        failure = e
    finally:
        if core is not None:
            core.shutdown()
    if failure is not None:
        if not dev:  # the message box is modal, so show it once cleanup is done
            _alert(
                f"Tamra could not start:\n\n{failure}\n\n"
                f"Details: {data_dir() / 'logs' / 'tamra.log'}"
            )
        raise failure


def _wait_in_dev_mode(port: int, token: str, thread: threading.Thread) -> None:
    print(f"Tamra core on http://127.0.0.1:{port} (token: {token})")
    print("Run `npm --prefix ui run dev` and open http://localhost:5173/#token=dev")
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        print("Stopping Tamra core...")


def _show_window(port: int, token: str, picker: FolderPicker) -> None:
    import webview  # heavy; not needed in dev mode or for selfcheck

    picker.window = webview.create_window(
        "Tamra",
        f"http://127.0.0.1:{port}/#token={token}",
        width=1200,
        height=800,
        min_size=(800, 560),
    )
    webview.start()


def _alert(message: str) -> None:
    """A Windows message box: the windowed exe has no console to print to."""
    ctypes.windll.user32.MessageBoxW(None, message, "Tamra", 0x10)  # MB_ICONERROR
