import os
import sys
from pathlib import Path

APP_NAME = "Tamra"


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


def resource_dir() -> Path:
    """Root for bundled resources (ui/dist, vendor/llama): _MEIPASS when frozen, else repo root."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def models_dir() -> Path:
    """TAMRA_MODELS_DIR, else the repo's .models (from source), else data_dir()/models."""
    override = os.environ.get("TAMRA_MODELS_DIR", "").strip()
    if override:
        return Path(override).expanduser().absolute()
    if not getattr(sys, "frozen", False):
        dev = resource_dir() / ".models"
        if dev.is_dir():
            return dev
    return data_dir() / "models"
