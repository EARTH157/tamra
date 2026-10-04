import hashlib
import threading

import httpx
import pytest

from tamra.download import ChecksumError, DownloadCancelled, download_resumable, sha256_file

BIG = bytes(range(256)) * 400  # 102,400 bytes
BIG_SHA = hashlib.sha256(BIG).hexdigest()


class _Chunks(httpx.SyncByteStream):
    """A body sent in 4 KiB pieces, optionally failing after `fail_after` bytes."""

    def __init__(self, data: bytes, fail_after: int | None = None):
        self.data = data
        self.fail_after = fail_after

    def __iter__(self):
        sent = 0
        for i in range(0, len(self.data), 4096):
            if self.fail_after is not None and sent >= self.fail_after:
                raise httpx.ReadError("connection dropped")
            piece = self.data[i : i + 4096]
            sent += len(piece)
            yield piece


class RangeServer:
    """Serves BIG with Range support; records the Range header of each request."""

    def __init__(self, data: bytes = BIG, honour_range: bool = True):
        self.data = data
        self.honour_range = honour_range
        self.ranges: list[str | None] = []
        self.fail_first_after: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range")
        self.ranges.append(rng)
        fail_after, self.fail_first_after = self.fail_first_after, None
        if rng and self.honour_range:
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            body = self.data[start:]
            return httpx.Response(
                206,
                headers={
                    "Content-Range": f"bytes {start}-{len(self.data) - 1}/{len(self.data)}",
                    "Content-Length": str(len(body)),
                },
                stream=_Chunks(body, fail_after),
            )
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(self.data))},
            stream=_Chunks(self.data, fail_after),
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _no_request(request):
    raise AssertionError("should not download")


def test_downloads_from_scratch(tmp_path):
    server = RangeServer()
    dest = tmp_path / "m" / "file.bin"
    got = download_resumable(
        "https://x/f", dest, BIG_SHA, size=len(BIG), transport=server.transport()
    )
    assert got == dest
    assert dest.read_bytes() == BIG
    assert not dest.with_name("file.bin.part").exists()
    assert server.ranges == [None]


def test_resumes_an_interrupted_download_with_a_range_request(tmp_path):
    server = RangeServer()
    server.fail_first_after = 30_000
    dest = tmp_path / "file.bin"
    with pytest.raises(httpx.ReadError):
        download_resumable(
            "https://x/f", dest, BIG_SHA, size=len(BIG), transport=server.transport()
        )
    part = dest.with_name("file.bin.part")
    kept = part.stat().st_size
    assert 0 < kept < len(BIG)
    assert not dest.exists()

    download_resumable("https://x/f", dest, BIG_SHA, size=len(BIG), transport=server.transport())
    assert server.ranges == [None, f"bytes={kept}-"]
    assert dest.read_bytes() == BIG
    assert sha256_file(dest) == BIG_SHA
    assert not part.exists()


def test_a_server_that_ignores_range_restarts_the_download(tmp_path):
    server = RangeServer(honour_range=False)
    dest = tmp_path / "file.bin"
    part = dest.with_name("file.bin.part")
    part.write_bytes(b"stale-prefix")
    seen: list[tuple[int, int]] = []
    download_resumable(
        "https://x/f",
        dest,
        BIG_SHA,
        size=len(BIG),
        on_progress=lambda done, total: seen.append((done, total)),
        transport=server.transport(),
    )
    assert server.ranges == [f"bytes={len(b'stale-prefix')}-"]
    assert dest.read_bytes() == BIG
    assert seen[-1] == (len(BIG), len(BIG))
    assert all(done <= len(BIG) for done, _ in seen)


def test_checksum_mismatch_deletes_the_part_and_raises(tmp_path):
    dest = tmp_path / "file.bin"
    with pytest.raises(ChecksumError):
        download_resumable(
            "https://x/f", dest, "0" * 64, size=len(BIG), transport=RangeServer().transport()
        )
    assert list(tmp_path.iterdir()) == []


def test_a_resumed_download_with_a_corrupt_prefix_fails_verification(tmp_path):
    dest = tmp_path / "file.bin"
    part = dest.with_name("file.bin.part")
    part.write_bytes(b"\xff" * 1000)
    with pytest.raises(ChecksumError):
        download_resumable("https://x/f", dest, BIG_SHA, transport=RangeServer().transport())
    assert not part.exists()
    assert not dest.exists()


def test_cancel_keeps_the_part_and_raises(tmp_path):
    dest = tmp_path / "file.bin"
    cancel = threading.Event()

    def on_progress(done: int, total: int) -> None:
        if done >= 8192:
            cancel.set()

    with pytest.raises(DownloadCancelled):
        download_resumable(
            "https://x/f",
            dest,
            BIG_SHA,
            size=len(BIG),
            on_progress=on_progress,
            cancel=cancel,
            transport=RangeServer().transport(),
        )
    part = dest.with_name("file.bin.part")
    assert 8192 <= part.stat().st_size < len(BIG)
    assert not dest.exists()
    assert BIG.startswith(part.read_bytes())


def test_cancel_before_start_makes_no_request(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        download_resumable(
            "https://x/f",
            tmp_path / "file.bin",
            BIG_SHA,
            cancel=cancel,
            transport=httpx.MockTransport(_no_request),
        )


def test_an_uppercase_sha256_is_accepted(tmp_path):
    dest = tmp_path / "file.bin"
    download_resumable(
        "https://x/f", dest, BIG_SHA.upper(), size=len(BIG), transport=RangeServer().transport()
    )
    assert dest.read_bytes() == BIG


def test_progress_arrives_in_order_and_ends_at_the_total(tmp_path):
    seen: list[tuple[int, int]] = []
    download_resumable(
        "https://x/f",
        tmp_path / "file.bin",
        BIG_SHA,
        size=len(BIG),
        on_progress=lambda done, total: seen.append((done, total)),
        transport=RangeServer().transport(),
    )
    dones = [d for d, _ in seen]
    assert dones == sorted(dones)
    assert len(set(dones)) > 3
    assert seen[-1] == (len(BIG), len(BIG))


def test_total_comes_from_the_response_when_size_is_not_given(tmp_path):
    seen: list[tuple[int, int]] = []
    download_resumable(
        "https://x/f",
        tmp_path / "file.bin",
        BIG_SHA,
        on_progress=lambda done, total: seen.append((done, total)),
        transport=RangeServer().transport(),
    )
    assert seen[-1] == (len(BIG), len(BIG))


def test_an_existing_verified_dest_is_not_downloaded_again(tmp_path):
    dest = tmp_path / "file.bin"
    dest.write_bytes(BIG)
    got = download_resumable(
        "https://x/f", dest, BIG_SHA.upper(), transport=httpx.MockTransport(_no_request)
    )
    assert got == dest


def test_a_complete_part_is_verified_without_a_request(tmp_path):
    dest = tmp_path / "file.bin"
    dest.with_name("file.bin.part").write_bytes(BIG)
    download_resumable(
        "https://x/f", dest, BIG_SHA, size=len(BIG), transport=httpx.MockTransport(_no_request)
    )
    assert dest.read_bytes() == BIG
    assert not dest.with_name("file.bin.part").exists()


def test_http_416_restarts_from_zero(tmp_path):
    dest = tmp_path / "file.bin"
    dest.with_name("file.bin.part").write_bytes(b"x" * (len(BIG) + 10))
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("range"))
        if request.headers.get("range"):
            return httpx.Response(416)
        return httpx.Response(200, content=BIG)

    download_resumable("https://x/f", dest, BIG_SHA, transport=httpx.MockTransport(handler))
    assert calls == [f"bytes={len(BIG) + 10}-", None]
    assert dest.read_bytes() == BIG


def test_a_part_longer_than_the_known_size_is_discarded(tmp_path):
    server = RangeServer()
    dest = tmp_path / "file.bin"
    dest.with_name("file.bin.part").write_bytes(b"x" * (len(BIG) + 10))
    download_resumable("https://x/f", dest, BIG_SHA, size=len(BIG), transport=server.transport())
    assert server.ranges == [None]
    assert dest.read_bytes() == BIG


def test_http_errors_keep_the_part(tmp_path):
    dest = tmp_path / "file.bin"
    part = dest.with_name("file.bin.part")
    part.write_bytes(BIG[:100])
    with pytest.raises(httpx.HTTPStatusError):
        download_resumable(
            "https://x/f",
            dest,
            BIG_SHA,
            transport=httpx.MockTransport(lambda r: httpx.Response(503)),
        )
    assert part.read_bytes() == BIG[:100]
