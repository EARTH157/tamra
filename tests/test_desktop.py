"""tamra.desktop (Ubuntu support spec §1.3-1.5)."""

import stat
import subprocess
import sys

import pytest

from tamra import desktop

linux = pytest.mark.skipif(sys.platform != "linux", reason="Linux's desktop")


def test_one_instance_per_data_folder(tmp_path):
    first = desktop.single_instance(tmp_path)
    try:
        assert first is not None
        assert desktop.single_instance(tmp_path) is None
        (tmp_path / "other").mkdir()
        other = desktop.single_instance(tmp_path / "other")  # another data folder is independent
        assert other is not None
        desktop.release_instance(other)
    finally:
        desktop.release_instance(first)
    again = desktop.single_instance(tmp_path)  # releasing freed it
    assert again is not None
    desktop.release_instance(again)


@linux
def test_the_lock_file_is_private_and_not_inherited(tmp_path):
    import fcntl

    handle = desktop.single_instance(tmp_path)
    try:
        lock = tmp_path / "tamra.lock"
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600
        # Closed on exec: a llama-server that outlived Tamra must never hold Tamra's lock.
        assert fcntl.fcntl(handle, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
    finally:
        desktop.release_instance(handle)


@linux
def test_open_path_runs_xdg_open_without_a_shell(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **k: calls.append((args, k)))
    desktop.open_path(tmp_path / "a b.pdf")
    args, options = calls[0]
    assert args == ["xdg-open", str(tmp_path / "a b.pdf")]
    assert options["start_new_session"] is True
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL


@linux
def test_alert_uses_zenity_without_markup(monkeypatch):
    runs = []
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/zenity")
    monkeypatch.setattr(desktop.subprocess, "run", lambda args, **k: runs.append(args))
    desktop.alert("Tamra could not start: <b>x</b>")
    assert runs == [
        [
            "/usr/bin/zenity",
            "--error",
            "--no-markup",
            "--title=Tamra",
            "--text=Tamra could not start: <b>x</b>",
        ]
    ]


@linux
def test_the_linux_window_is_qt_and_matches_the_desktop_file(monkeypatch):
    monkeypatch.delenv("QT_API", raising=False)
    assert desktop.window_options() == {"gui": "qt"}
    assert desktop.os.environ["QT_API"] == "pyside6"
    from qtpy.QtGui import QGuiApplication

    assert QGuiApplication.desktopFileName() == "tamra"  # GNOME shows tamra.desktop's icon


@pytest.mark.skipif(sys.platform != "win32", reason="WebView2")
def test_the_windows_window_needs_no_options():
    assert desktop.window_options() == {}


@linux
def test_alert_without_zenity_writes_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    desktop.alert("Tamra is already running.")
    assert capsys.readouterr().err == "Tamra is already running.\n"
