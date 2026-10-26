"""Which kind of drive a path is on, so a model on a USB stick is copied rather than linked."""

import ctypes
import os
import sys
from pathlib import Path

_REMOVABLE, _CDROM = 2, 5  # GetDriveTypeW results
_LINUX_REMOVABLE_ROOTS = (Path("/media"), Path("/run/media"))  # where udisks2 mounts USB drives


def _drive_type(root: str) -> int:
    return ctypes.windll.kernel32.GetDriveTypeW(root)  # AttributeError off Windows


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
