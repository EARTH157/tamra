import stat
import sys
from pathlib import Path

import pytest

from tamra import paths

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows' data folder")
posix = pytest.mark.skipif(sys.platform == "win32", reason="Linux's data folder and modes")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_data_dir_uses_override_and_creates_it(monkeypatch, tmp_path):
    target = tmp_path / "custom"
    monkeypatch.setenv("TAMRA_DATA_DIR", str(target))
    assert paths.data_dir() == target
    assert target.is_dir()


@posix
def test_data_dir_defaults_to_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.data_dir() == tmp_path / "xdg" / "tamra"


@posix
def test_without_xdg_data_home_the_data_dir_is_in_local_share(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.data_dir() == tmp_path / ".local" / "share" / "tamra"


@posix
def test_a_relative_xdg_data_home_and_a_blank_override_are_ignored(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAMRA_DATA_DIR", "  ")
    monkeypatch.setenv("XDG_DATA_HOME", "rel")  # the XDG spec says to ignore a relative path
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert paths.data_dir() == tmp_path / "home" / ".local" / "share" / "tamra"


@posix
def test_the_default_data_dir_is_kept_private(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    folder = paths.data_dir()
    assert mode(folder) == 0o700
    folder.chmod(0o755)
    paths.data_dir()
    assert mode(folder) == 0o700


@posix
def test_a_new_override_is_private_and_an_existing_one_keeps_its_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "new"))
    assert mode(paths.data_dir()) == 0o700
    existing = tmp_path / "existing"
    existing.mkdir()
    existing.chmod(0o755)
    monkeypatch.setenv("TAMRA_DATA_DIR", str(existing))
    assert mode(paths.data_dir()) == 0o755  # the user chose this folder


def test_llama_server_is_this_platforms_bundled_build():
    name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    assert paths.llama_server() == paths.resource_dir() / "vendor" / "llama" / name


@windows
def test_data_dir_defaults_to_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "Tamra"


def test_resource_dir_is_repo_root_from_source():
    assert (paths.resource_dir() / "pyproject.toml").is_file()
    assert isinstance(paths.resource_dir(), Path)


@windows
def test_a_blank_override_falls_back_to_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", "  ")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "Tamra"


def test_a_relative_override_is_made_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAMRA_DATA_DIR", "rel")
    assert paths.data_dir() == tmp_path / "rel"


@windows
def test_a_missing_localappdata_says_what_to_do(monkeypatch):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError, match="TAMRA_DATA_DIR"):
        paths.data_dir()


def test_resource_dir_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.resource_dir() == tmp_path


def test_models_dir_prefers_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_MODELS_DIR", str(tmp_path / "m"))
    assert paths.models_dir() == tmp_path / "m"


def test_models_dir_uses_the_repo_models_from_source(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_MODELS_DIR", raising=False)
    monkeypatch.setattr(paths, "resource_dir", lambda: tmp_path)
    (tmp_path / ".models").mkdir()
    assert paths.models_dir() == tmp_path / ".models"


def test_models_dir_defaults_to_the_data_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_MODELS_DIR", raising=False)
    monkeypatch.setattr(paths, "resource_dir", lambda: tmp_path)
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    assert paths.models_dir() == tmp_path / "data" / "models"
