import hashlib

import httpx
import pytest

from tamra.download import ChecksumError, download_verified, sha256_file

PAYLOAD = b"tamra" * 1000
GOOD = hashlib.sha256(PAYLOAD).hexdigest()


def serve(payload: bytes) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, content=payload))


def test_downloads_and_verifies(tmp_path):
    dest = tmp_path / "sub" / "file.bin"
    assert download_verified("https://x/file", dest, GOOD, transport=serve(PAYLOAD)) == dest
    assert dest.read_bytes() == PAYLOAD
    assert sha256_file(dest) == GOOD
    assert not dest.with_name("file.bin.part").exists()


def test_checksum_mismatch_raises_and_leaves_nothing(tmp_path):
    dest = tmp_path / "file.bin"
    with pytest.raises(ChecksumError):
        download_verified("https://x/file", dest, "0" * 64, transport=serve(PAYLOAD))
    assert list(tmp_path.iterdir()) == []


def test_existing_valid_file_is_not_downloaded_again(tmp_path):
    dest = tmp_path / "file.bin"
    dest.write_bytes(PAYLOAD)

    def fail(request):
        raise AssertionError("should not download")

    download_verified("https://x/file", dest, GOOD, transport=httpx.MockTransport(fail))
