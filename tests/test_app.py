import threading

import pytest
import webview

from tamra import app
from tamra.net import free_port


class FakeWindow:
    def __init__(self, result):
        self.result = result
        self.kinds = []

    def create_file_dialog(self, kind):
        self.kinds.append(kind)
        return self.result


def test_the_folder_picker_returns_the_chosen_folder():
    picker = app.FolderPicker()
    assert picker() is None  # no window yet
    picker.window = FakeWindow(("C:/docs",))
    assert picker() == "C:/docs"
    assert picker.window.kinds == [webview.FileDialog.FOLDER]


def test_the_folder_picker_handles_cancel():
    picker = app.FolderPicker()
    picker.window = FakeWindow(None)
    assert picker() is None


def test_wait_until_up_notices_a_dead_server_thread():
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    with pytest.raises(RuntimeError, match="already in use"):
        app._wait_until_up(free_port(), "token", dead, timeout=5)


def test_build_core_uses_the_configured_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAMRA_MODELS_DIR", str(tmp_path / "models"))
    core = app.build_core()
    try:
        assert (tmp_path / "data" / "tamra.db").exists()
        assert core.llm.label == "qwen2.5-0.5b-instruct-q4_k_m"
    finally:
        core.shutdown()
