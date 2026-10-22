import sys
from pathlib import Path

import pytest

import tamra.drives as drives
from tamra.drives import is_removable


@pytest.mark.parametrize(
    ("kind", "removable"), [(2, True), (5, True), (3, False), (4, False), (0, False)]
)
def test_only_removable_and_cd_drives_count_as_removable(tmp_path, monkeypatch, kind, removable):
    monkeypatch.setattr(drives, "_drive_type", lambda root: kind)
    assert is_removable(tmp_path / "m.gguf") is removable


def test_the_drive_root_is_what_windows_is_asked_about(monkeypatch):
    asked = []
    monkeypatch.setattr(drives, "_drive_type", lambda root: asked.append(root) or 3)
    is_removable(Path("E:\\Models\\m.gguf"))
    is_removable(Path("\\\\host\\share\\m.gguf"))
    assert asked == ["E:\\", "\\\\host\\share\\"]


def test_a_failing_check_counts_as_a_fixed_drive(tmp_path, monkeypatch):
    def broken(root):
        raise OSError("no")

    monkeypatch.setattr(drives, "_drive_type", broken)
    assert is_removable(tmp_path / "m.gguf") is False


@pytest.mark.skipif(sys.platform != "win32", reason="asks Windows")
def test_the_temp_folder_is_not_on_a_removable_drive(tmp_path):
    assert is_removable(tmp_path) is False
