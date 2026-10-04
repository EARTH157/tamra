import logging
import threading

import pytest
import webview

from tamra import app
from tamra.logs import shutdown_logging
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


class RecordingWindow(FakeWindow):
    def create_file_dialog(self, kind, **options):
        self.kinds.append((kind, options))
        return self.result


def test_the_file_picker_asks_for_one_gguf_file():
    picker = app.FilePicker()
    assert picker() is None  # no window yet
    picker.window = RecordingWindow(("C:/models/m.gguf",))
    assert picker() == "C:/models/m.gguf"
    assert picker.window.kinds == [
        (webview.FileDialog.OPEN, {"allow_multiple": False, "file_types": ("GGUF model (*.gguf)",)})
    ]


def test_the_file_picker_handles_cancel():
    picker = app.FilePicker()
    picker.window = RecordingWindow(None)
    assert picker() is None


def test_open_data_folder_opens_only_the_data_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    opened = []
    monkeypatch.setattr(app.os, "startfile", opened.append, raising=False)  # never the real one
    app.open_data_folder()
    assert opened == [tmp_path / "data"]


def test_wait_until_up_notices_a_dead_server_thread():
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    with pytest.raises(RuntimeError, match="already in use"):
        app._wait_until_up(free_port(), "token", dead, timeout=5)


def test_build_core_uses_the_configured_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAMRA_MODELS_DIR", str(tmp_path / "models"))
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "some-model.gguf").write_bytes(b"GGUF")
    core = app.build_core()
    try:
        assert (tmp_path / "data" / "tamra.db").exists()
        assert core.local.label == "some-model"  # no catalog model is installed: any GGUF
    finally:
        core.shutdown()


def test_a_failing_core_is_logged_and_shown_after_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv("TAMRA_DATA_DIR", str(tmp_path))
    alerts = []
    started = []

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "build_core", boom)
    monkeypatch.setattr(app, "_alert", alerts.append)
    monkeypatch.setattr(app, "create_app", lambda *a, **k: started.append("api"))
    monkeypatch.setattr(app, "_show_window", lambda *a, **k: started.append("window"))
    try:
        with pytest.raises(RuntimeError, match="boom"):
            app.run(dev=False)
        logging.getLogger().handlers[0].flush()
        assert "boom" in (tmp_path / "logs" / "tamra.log").read_text(encoding="utf-8")
    finally:
        shutdown_logging()
    assert len(alerts) == 1
    assert "boom" in alerts[0]
    assert started == []
