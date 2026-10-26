"""Desktop entry: start Core and the API on 127.0.0.1, then show the window (or dev mode)."""

import logging
import secrets
import threading
import time
from pathlib import Path

import httpx
import truststore
import uvicorn

from tamra.core import Core
from tamra.desktop import alert as _alert
from tamra.desktop import open_path
from tamra.desktop import release_instance as _release_instance
from tamra.desktop import single_instance as _single_instance
from tamra.discovery import remove_discovery, write_discovery
from tamra.logs import setup_logging
from tamra.net import free_port
from tamra.paths import data_dir, llama_server, models_dir, resource_dir
from tamra.server import create_app

log = logging.getLogger(__name__)

DEV_PORT = 8765
DEV_TOKEN = "dev"

_instance_lock = None  # held for the process lifetime: the OS frees it when the process exits


def build_core() -> Core:
    """Core with data in data_dir(), models in models_dir(), and the bundled llama-server."""
    return Core(data_dir(), models_dir(), llama_server(), warm_attribution=True)


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


class FilePicker:
    """The Windows file picker for a GGUF model, on the app window (called from a worker thread)."""

    FILE_TYPES = ("GGUF model (*.gguf)",)

    def __init__(self) -> None:
        self.window = None  # set once the window exists

    def __call__(self) -> str | None:
        if self.window is None:
            return None
        import webview

        chosen = self.window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=False, file_types=self.FILE_TYPES
        )
        return str(chosen[0]) if chosen else None


def open_data_folder() -> None:
    """Show Tamra's data folder in the file manager. The path is never taken from a request."""
    open_path(data_dir())


def open_external(path: Path) -> None:
    """Open a file in its default app. The server passes only paths it resolved from the store."""
    open_path(path)


def _publish_mcp(port: int, token: str) -> None:
    """Tell `Tamra.exe mcp` where the API is. Without the file AI apps only see "not running",
    so a failure is logged and Tamra carries on."""
    try:
        write_discovery(data_dir(), port, token)
    except OSError as e:
        log.warning("cannot write the MCP discovery file: %s", e)


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


def run(dev: bool = False, token: str | None = None) -> None:
    """Start Tamra. In dev mode the API listens on DEV_PORT with `token` (default "dev")."""
    global _instance_lock
    _instance_lock = _single_instance(data_dir())
    if _instance_lock is None:
        if dev:
            raise RuntimeError("Tamra is already running")
        _alert("Tamra is already running.")
        return
    try:
        _serve(dev, token or DEV_TOKEN)
    finally:
        _release_instance(_instance_lock)
        _instance_lock = None


def _serve(dev: bool, dev_token: str) -> None:
    truststore.inject_into_ssl()  # downloads and cloud calls trust the Windows certificate store
    setup_logging(data_dir() / "logs", console=dev)
    port, token = (DEV_PORT, dev_token) if dev else (free_port(), secrets.token_urlsafe(32))
    mcp_token = secrets.token_urlsafe(32)
    ui_dir = None if dev else resource_dir() / "ui" / "dist"
    picker = None if dev else FolderPicker()
    file_picker = None if dev else FilePicker()
    core: Core | None = None
    failure: Exception | None = None
    try:
        core = build_core()
        core.start()
        config = uvicorn.Config(
            create_app(
                token,
                ui_dir,
                core,
                pick_folder=picker,
                pick_file=file_picker,
                open_data_folder=None if dev else open_data_folder,
                open_external=None if dev else open_external,
                mcp_token=mcp_token,
            ),
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
            _publish_mcp(port, mcp_token)
            if picker is None:
                _wait_in_dev_mode(port, token, thread)
            else:
                _show_window(port, token, picker, file_picker)
        finally:
            remove_discovery(data_dir())
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
    shown = token if token == DEV_TOKEN else "from TAMRA_DEV_TOKEN"  # never echo a chosen secret
    print(f"Tamra core on http://127.0.0.1:{port} (token: {shown})")
    print(f"Run `npm --prefix ui run dev` and open http://localhost:5173/#token={shown}")
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        print("Stopping Tamra core...")


def _show_window(
    port: int, token: str, picker: FolderPicker, file_picker: FilePicker | None = None
) -> None:
    import webview  # heavy; not needed in dev mode or for selfcheck

    picker.window = webview.create_window(
        "Tamra",
        f"http://127.0.0.1:{port}/#token={token}",
        width=1200,
        height=800,
        min_size=(800, 560),
        text_select=True,  # answers and passages are selected to check them against the source
    )
    if file_picker is not None:
        file_picker.window = picker.window
    # pywebview otherwise uses sys.executable's icon, which is python.exe's when run from source.
    webview.start(icon=str(resource_dir() / "packaging" / "tamra.ico"))
