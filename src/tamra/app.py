import secrets
import threading
import time

import httpx
import uvicorn

from tamra.net import free_port
from tamra.paths import resource_dir
from tamra.server import create_app

DEV_PORT = 8765
DEV_TOKEN = "dev"


def _wait_until_up(port: int, token: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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
    port, token = (DEV_PORT, DEV_TOKEN) if dev else (free_port(), secrets.token_urlsafe(32))
    ui_dir = None if dev else resource_dir() / "ui" / "dist"
    config = uvicorn.Config(
        create_app(token, ui_dir), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_up(port, token)

    if dev:
        print(f"Tamra core on http://127.0.0.1:{port} (token: {token})")
        print("Run `npm run dev` in ui/ and open http://localhost:5173/#token=dev")
        while thread.is_alive():
            thread.join(0.5)
        return

    import webview  # imported lazily: heavy, and not needed for dev mode or selfcheck

    webview.create_window(
        "Tamra", f"http://127.0.0.1:{port}/#token={token}", width=1200, height=800
    )
    webview.start()
    server.should_exit = True
    thread.join(timeout=5)
