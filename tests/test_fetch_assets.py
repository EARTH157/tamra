"""scripts/fetch_assets.py: this platform's assets, and archives unpacked safely."""

import io
import sys
import tarfile
from pathlib import Path

import pytest
from fetch_assets import extract, select

ASSETS = {
    "llama-windows": {"platform": "win32"},
    "llama-linux": {"platform": "linux"},
    "bge-m3-model": {},
}


def test_llama_means_this_platforms_build():
    assert select(ASSETS, ["llama"], "win32") == ["llama-windows"]
    assert select(ASSETS, ["llama", "bge-m3-model"], "linux") == ["llama-linux", "bge-m3-model"]


def test_no_names_means_every_asset_for_this_platform():
    assert select(ASSETS, [], "linux") == ["llama-linux", "bge-m3-model"]


def test_an_unknown_name_is_refused():
    with pytest.raises(KeyError):
        select(ASSETS, ["nope"], "linux")


def _tar(path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for info, data in members:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def _file(name: str, data: bytes, mode: int = 0o644) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), mode
    return info, data


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks and modes")
def test_a_single_top_folder_is_dropped_and_modes_and_links_are_kept(tmp_path):
    top = tarfile.TarInfo("llama-b1/")
    top.type = tarfile.DIRTYPE
    link = tarfile.TarInfo("llama-b1/libggml.so")
    link.type, link.linkname = tarfile.SYMTYPE, "libggml.so.0"
    archive = _tar(
        tmp_path / "a.tar.gz",
        [
            (top, None),
            _file("llama-b1/llama-server", b"ELF", 0o755),
            _file("llama-b1/libggml.so.0", b"so"),
            (link, None),
        ],
    )
    extract(archive, tmp_path / "out")
    server = tmp_path / "out" / "llama-server"
    assert server.read_bytes() == b"ELF"
    assert server.stat().st_mode & 0o111  # still executable
    assert (tmp_path / "out" / "libggml.so").is_symlink()
    assert (tmp_path / "out" / "libggml.so").read_bytes() == b"so"


def test_a_member_outside_the_target_is_refused(tmp_path):
    archive = _tar(tmp_path / "evil.tar.gz", [_file("../evil.txt", b"x")])
    with pytest.raises(tarfile.FilterError):
        extract(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()
