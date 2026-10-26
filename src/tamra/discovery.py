"""The MCP discovery file: how `Tamra.exe mcp` finds the running app's port and MCP token
(docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §4).

It lives in the data folder, which only the user can read. The token in it opens only the
read-only /api/mcp routes, and only while the user has turned AI access on.
"""

import json
import os
from contextlib import suppress
from pathlib import Path

FILE_NAME = "mcp.json"


def write_discovery(folder: Path, port: int, token: str) -> None:
    """Write the file atomically (a reader sees the old content or the new, never half), readable
    only by this user even in a data folder that others can read."""
    path = folder / FILE_NAME
    temporary = path.with_name(FILE_NAME + ".tmp")
    temporary.unlink(missing_ok=True)  # a leftover could carry a wider mode
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"port": port, "token": token}))
    os.replace(temporary, path)


def read_discovery(folder: Path) -> tuple[int, str] | None:
    """The port and token, or None when the file is missing or damaged."""
    try:
        data = json.loads((folder / FILE_NAME).read_text(encoding="utf-8"))
        port, token = data["port"], data["token"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return None
    if not isinstance(token, str) or not token:
        return None
    return port, token


def remove_discovery(folder: Path) -> None:
    """Best effort: a file left behind is replaced by the next launch."""
    with suppress(OSError):
        (folder / FILE_NAME).unlink(missing_ok=True)
