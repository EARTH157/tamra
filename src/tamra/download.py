import hashlib
from pathlib import Path

import httpx


class ChecksumError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download_verified(
    url: str, dest: Path, sha256: str, transport: httpx.BaseTransport | None = None
) -> Path:
    """Download url to dest, verifying sha256. Never leaves an unverified file at dest."""
    if dest.exists() and sha256_file(dest) == sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    timeout = httpx.Timeout(30.0, read=300.0)
    with httpx.Client(follow_redirects=True, timeout=timeout, transport=transport) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with part.open("wb") as f:
                for chunk in response.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
    if h.hexdigest() != sha256:
        part.unlink()
        raise ChecksumError(f"{url}: expected {sha256}, got {h.hexdigest()}")
    part.replace(dest)
    return dest
