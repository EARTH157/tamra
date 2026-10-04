import sys
from pathlib import Path

import pytest

from tamra import paths


def test_data_dir_uses_override_and_creates_it(monkeypatch, tmp_path):
    target = tmp_path / "custom"
    monkeypatch.setenv("TAMRA_DATA_DIR", str(target))
    assert paths.data_dir() == target
    assert target.is_dir()


def test_data_dir_defaults_to_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMRA_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "Tamra"


def test_resource_dir_is_repo_root_from_source():
    assert (paths.resource_dir() / "pyproject.toml").is_file()
    assert isinstance(paths.resource_dir(), Path)


def test_a_blank_override_falls_back_to_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", "  ")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "Tamra"


def test_a_relative_override_is_made_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAMRA_DATA_DIR", "rel")
    assert paths.data_dir() == tmp_path / "rel"


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
