import os
import sys
from pathlib import Path

APP_NAME = "Tamra"


def data_dir() -> Path:
    """Per-user data directory (%LOCALAPPDATA%\\Tamra), overridable via TAMRA_DATA_DIR."""
    override = os.environ.get("TAMRA_DATA_DIR")
    base = Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def resource_dir() -> Path:
    """Root for bundled resources (ui/dist, vendor/llama): _MEIPASS when frozen, else repo root."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]
