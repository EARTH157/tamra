import time

from tamra.ingest.watcher import FolderWatcher


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_a_burst_of_changes_fires_once(tmp_path):
    calls = []
    watcher = FolderWatcher(lambda: calls.append(1), debounce=0.3)
    watcher.watch(tmp_path)
    try:
        for i in range(5):
            (tmp_path / f"f{i}.md").write_text("x", encoding="utf-8")
        assert wait_for(lambda: calls)
        time.sleep(0.8)
        assert len(calls) == 1
    finally:
        watcher.stop()


def test_office_lock_files_are_ignored(tmp_path):
    calls = []
    watcher = FolderWatcher(lambda: calls.append(1), debounce=0.2)
    watcher.watch(tmp_path)
    try:
        (tmp_path / "~$report.docx").write_bytes(b"lock")
        time.sleep(1.0)
        assert calls == []
    finally:
        watcher.stop()


def test_a_stopped_watcher_stays_quiet(tmp_path):
    calls = []
    watcher = FolderWatcher(lambda: calls.append(1), debounce=0.2)
    watcher.watch(tmp_path)
    watcher.stop()
    (tmp_path / "late.md").write_text("x", encoding="utf-8")
    time.sleep(0.8)
    assert calls == []


def test_watch_moves_to_the_new_folder(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    calls = []
    watcher = FolderWatcher(lambda: calls.append(1), debounce=0.2)
    watcher.watch(first)
    watcher.watch(second)
    try:
        (first / "x.md").write_text("x", encoding="utf-8")
        time.sleep(0.8)
        assert calls == []
        (second / "y.md").write_text("y", encoding="utf-8")
        assert wait_for(lambda: calls)
    finally:
        watcher.stop()
