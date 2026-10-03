"""File logging under data_dir()/logs. The windowed exe has no console, so this is its record."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_installed: list[logging.Handler] = []
_previous_level: list[int] = []


def setup_logging(log_dir: Path, *, console: bool = False) -> Path:
    """Send INFO and above to log_dir/tamra.log (rotated); optionally also to the console."""
    shutdown_logging()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "tamra.log"
    handlers: list[logging.Handler] = [
        RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if console:
        handlers.append(logging.StreamHandler())
    root = logging.getLogger()
    _previous_level.append(root.level)
    root.setLevel(logging.INFO)
    for handler in handlers:
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
        _installed.append(handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # the UI polls status
    return path


def shutdown_logging() -> None:
    """Remove and close the handlers setup_logging() added, and restore the root level."""
    root = logging.getLogger()
    while _installed:
        handler = _installed.pop()
        root.removeHandler(handler)
        handler.close()
    if _previous_level:
        root.setLevel(_previous_level[0])
        _previous_level.clear()
