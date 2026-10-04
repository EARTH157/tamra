import hashlib
import threading
from collections.abc import Callable
from pathlib import Path

import httpx


class ChecksumError(Exception):
    pass


class DownloadCancelled(Exception):
    """The caller set the cancel event. The .part file is kept so the download can resume."""


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


def download_resumable(
    url: str,
    dest: Path,
    sha256: str,
    *,
    size: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Download url to dest, continuing dest.part with a Range request when it exists.

    on_progress(done, total) is called as bytes arrive; total is 0 when it is not known. A server
    that ignores Range (status 200) restarts the download. On a hash mismatch the .part is deleted
    and ChecksumError is raised; on cancel it is kept and DownloadCancelled is raised. Transport
    and HTTP errors also keep it. An unverified file is never left at dest.
    """
    expected = sha256.lower()

    def progress(done: int, total: int) -> None:
        if on_progress is not None:
            on_progress(done, total)

    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled(url)

    if dest.is_file() and (size is None or dest.stat().st_size == size):
        if sha256_file(dest) == expected:
            total = dest.stat().st_size
            progress(total, total)
            return dest
    check_cancel()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    offset = part.stat().st_size if part.is_file() else 0
    if size is not None and offset > size:
        part.unlink()
        offset = 0

    h = hashlib.sha256()
    if offset:
        _hash_into(h, part)
    if size is not None and offset == size:
        progress(size, size)
        done = offset
    else:
        h, done = _transfer(url, part, h, offset, size, progress, check_cancel, transport)
    digest = h.hexdigest()
    if size is not None and done < size and digest != expected:
        # The stream ended early without an error: keep the .part so the next attempt resumes.
        raise httpx.ReadError(f"{url}: received {done} of {size} bytes")
    if digest != expected:
        part.unlink()
        raise ChecksumError(f"{url}: expected {expected}, got {digest}")
    part.replace(dest)
    return dest


def _hash_into(h: "hashlib._Hash", path: Path) -> None:
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)


def _transfer(
    url: str,
    part: Path,
    h: "hashlib._Hash",
    offset: int,
    size: int | None,
    progress: Callable[[int, int], None],
    check_cancel: Callable[[], None],
    transport: httpx.BaseTransport | None,
) -> tuple["hashlib._Hash", int]:
    """Append the response body to part, restarting from zero if the server will not resume."""
    timeout = httpx.Timeout(30.0, read=300.0)
    with httpx.Client(follow_redirects=True, timeout=timeout, transport=transport) as client:
        for _ in range(2):
            headers = {"Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416 and offset:
                    part.unlink(missing_ok=True)  # the part does not fit the file: start over
                    offset, h = 0, hashlib.sha256()
                    continue
                response.raise_for_status()
                if offset and response.status_code != 206:
                    offset, h = 0, hashlib.sha256()  # Range ignored: the body is the whole file
                total = size if size is not None else _total_bytes(response, offset)
                done = offset
                progress(done, total)
                with part.open("ab" if offset else "wb") as f:
                    for chunk in response.iter_bytes():
                        check_cancel()
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        progress(done, total)
                return h, done
    raise httpx.HTTPError(f"{url}: the server rejected the range request twice")


def _total_bytes(response: httpx.Response, offset: int) -> int:
    """The full file size from Content-Range or Content-Length; 0 when the server did not say."""
    content_range = response.headers.get("content-range", "")
    if "/" in content_range:
        total = content_range.rsplit("/", 1)[1]
        if total.isdigit():
            return int(total)
    length = response.headers.get("content-length", "")
    return offset + int(length) if length.isdigit() else 0
