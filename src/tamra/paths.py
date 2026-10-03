import os
import sys
from pathlib import Path

APP_NAME = "Tamra"


def data_dir() -> Path:
    """Per-user data directory (%LOCALAPPDATA%\\Tamra), overridable via TAMRA_DATA_DIR."""
    override = os.environ.get("TAMRA_DATA_DIR", "").strip()
    if override:
        base = Path(override).expanduser().absolute()
    else:
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if not local:
            raise RuntimeError(
                "LOCALAPPDATA is not set; set TAMRA_DATA_DIR to the folder Tamra should use"
            )
        base = Path(local) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


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
