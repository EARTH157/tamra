from pathlib import Path

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
