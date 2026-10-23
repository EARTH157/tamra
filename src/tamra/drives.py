"""Which kind of drive a path is on, so a model on a USB stick is copied rather than linked."""

import ctypes
import os
from pathlib import Path

_REMOVABLE, _CDROM = 2, 5  # GetDriveTypeW results


def _drive_type(root: str) -> int:
    return ctypes.windll.kernel32.GetDriveTypeW(root)  # AttributeError off Windows


def is_removable(path: Path) -> bool:
    """True for a path on a removable or CD-ROM drive, which may be gone the next time Tamra
    starts. False when Windows cannot say, or off Windows: the file is then used in place."""
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    try:
        return _drive_type(root) in (_REMOVABLE, _CDROM)
    except (AttributeError, OSError):
        return False
