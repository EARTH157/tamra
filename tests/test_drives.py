import sys
from pathlib import Path

import pytest

import tamra.drives as drives
from tamra.drives import is_removable

windows = pytest.mark.skipif(sys.platform != "win32", reason="asks Windows")
linux = pytest.mark.skipif(sys.platform != "linux", reason="Linux mount points")


@windows
@pytest.mark.parametrize(
    ("kind", "removable"), [(2, True), (5, True), (3, False), (4, False), (0, False)]
)
def test_only_removable_and_cd_drives_count_as_removable(tmp_path, monkeypatch, kind, removable):
    monkeypatch.setattr(drives, "_drive_type", lambda root: kind)
    assert is_removable(tmp_path / "m.gguf") is removable


@windows
def test_the_drive_root_is_what_windows_is_asked_about(monkeypatch):
    asked = []
    monkeypatch.setattr(drives, "_drive_type", lambda root: asked.append(root) or 3)
    is_removable(Path("E:\\Models\\m.gguf"))
    is_removable(Path("\\\\host\\share\\m.gguf"))
    assert asked == ["E:\\", "\\\\host\\share\\"]


@windows
def test_a_failing_check_counts_as_a_fixed_drive(tmp_path, monkeypatch):
    def broken(root):
        raise OSError("no")

    monkeypatch.setattr(drives, "_drive_type", broken)
    assert is_removable(tmp_path / "m.gguf") is False


def test_the_temp_folder_is_not_on_a_removable_drive(tmp_path):
    assert is_removable(tmp_path) is False


@linux
@pytest.mark.parametrize(
    ("path", "removable"),
    [
        ("/media/me/USB/m.gguf", True),
        ("/run/media/me/USB/m.gguf", True),
        ("/home/me/Models/m.gguf", False),
        ("/mediafiles/m.gguf", False),
        ("/media", False),
    ],
)
def test_files_where_ubuntu_mounts_usb_drives_count_as_removable(path, removable):
    assert is_removable(Path(path)) is removable


@linux
def test_a_link_into_media_counts_as_removable(tmp_path, monkeypatch):
    monkeypatch.setattr(drives.os.path, "realpath", lambda p: "/media/me/USB/m.gguf")
    assert is_removable(tmp_path / "m.gguf") is True
