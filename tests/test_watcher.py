import threading
import time
from itertools import pairwise

import pytest

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
    watcher = FolderWatcher(lambda cid: calls.append(cid), debounce=0.3)
    watcher.watch(1, tmp_path)
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
    watcher = FolderWatcher(lambda cid: calls.append(cid), debounce=0.2)
    watcher.watch(1, tmp_path)
    try:
        (tmp_path / "~$report.docx").write_bytes(b"lock")
        time.sleep(1.0)
        assert calls == []
    finally:
        watcher.stop()


def test_a_stopped_watcher_stays_quiet(tmp_path):
    calls = []
    watcher = FolderWatcher(lambda cid: calls.append(cid), debounce=0.2)
    watcher.watch(1, tmp_path)
    watcher.stop()
    (tmp_path / "late.md").write_text("x", encoding="utf-8")
    time.sleep(0.8)
    assert calls == []


def test_watch_moves_to_the_new_folder(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    calls = []
    watcher = FolderWatcher(lambda cid: calls.append(cid), debounce=0.2)
    watcher.watch(1, first)
    watcher.watch(1, second)
    try:
        (first / "x.md").write_text("x", encoding="utf-8")
        time.sleep(0.8)
        assert calls == []
        (second / "y.md").write_text("y", encoding="utf-8")
        assert wait_for(lambda: calls)
    finally:
        watcher.stop()


def test_stop_inside_the_debounce_window_cancels_the_call(tmp_path):
    calls = []
    watcher = FolderWatcher(lambda cid: calls.append(cid), debounce=0.5)
    watcher.watch(1, tmp_path)
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    time.sleep(0.2)  # the change is seen, the debounce has not elapsed
    watcher.stop()
    time.sleep(1.0)
    assert calls == []


def test_changes_spaced_inside_the_debounce_fire_once_after_the_last(tmp_path):
    # The debounce is long next to the 0.15 s spacing. With 0.3 s, one write a busy CI runner
    # delayed to 0.4 s (antivirus scans each new file) rightly fired twice, and the test failed.
    debounce = 1.0
    calls = []
    watcher = FolderWatcher(lambda cid: calls.append(time.monotonic()), debounce=debounce)
    watcher.watch(1, tmp_path)
    try:
        written = []
        for i in range(3):
            written.append(time.monotonic())  # before the write: its events may arrive early
            (tmp_path / f"f{i}.md").write_text("x", encoding="utf-8")
            time.sleep(0.15)
        gaps = [later - earlier for earlier, later in pairwise(written)]
        assert max(gaps) < debounce / 2, f"writes too slow to test the debounce: {gaps}"
        assert wait_for(lambda: calls)
        time.sleep(debounce)  # a second call would come within one debounce
        assert len(calls) == 1
        # Firing off the first write would be about 0.3 s before this bound; allow timer slack.
        assert calls[0] >= written[-1] + debounce - 0.05
    finally:
        watcher.stop()


def test_watching_a_missing_folder_raises_and_leaves_nothing_running(tmp_path):
    watcher = FolderWatcher(lambda cid: None, debounce=0.2)
    threads_before = threading.active_count()
    with pytest.raises(OSError):
        watcher.watch(1, tmp_path / "missing")
    assert threading.active_count() == threads_before


def test_a_stale_timer_does_not_fire(tmp_path):
    calls = []
    watcher = FolderWatcher(lambda cid: calls.append(cid), debounce=30)
    watcher.watch(1, tmp_path)
    try:
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        assert wait_for(lambda: watcher._timers.get(1) is not None)
        # A timer that already expired when a newer one replaced it runs _fire late.
        stale = threading.Timer(0, watcher._fire, args=(1,))
        stale.start()
        stale.join()
        assert calls == []
        assert watcher._timers.get(1) is not None  # the current timer is still armed
    finally:
        watcher.stop()


def test_each_project_reports_its_own_changes(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    calls = []
    watcher = FolderWatcher(calls.append, debounce=0.2)
    watcher.watch(1, one)
    watcher.watch(2, two)
    try:
        (two / "y.md").write_text("y", encoding="utf-8")
        assert wait_for(lambda: calls)
        time.sleep(0.6)
        assert set(calls) == {2}  # a late "modified" event may report project two again
        (one / "x.md").write_text("x", encoding="utf-8")
        assert wait_for(lambda: 1 in calls)
        assert set(calls) == {1, 2}
    finally:
        watcher.stop()


def test_concurrent_watches_of_one_project_raise_nothing(tmp_path):
    watcher = FolderWatcher(lambda cid: None, debounce=0.2)
    failures = []

    def watch():
        try:
            for _ in range(5):
                watcher.watch(1, tmp_path)
        except BaseException as e:  # the test reports whatever a thread raised
            failures.append(e)

    try:
        threads = [threading.Thread(target=watch) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert failures == []
    finally:
        watcher.stop()


def test_unwatching_one_project_leaves_the_other_watched(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    calls = []
    watcher = FolderWatcher(calls.append, debounce=0.4)
    watcher.watch(1, one)
    watcher.watch(2, two)
    try:
        (one / "x.md").write_text("x", encoding="utf-8")
        assert wait_for(lambda: 1 in watcher._timers)  # seen, not yet reported
        watcher.unwatch(1)
        assert 1 not in watcher._timers  # its pending call is cancelled
        (one / "late.md").write_text("x", encoding="utf-8")
        (two / "y.md").write_text("y", encoding="utf-8")
        assert wait_for(lambda: calls)
        time.sleep(0.8)
        assert calls == [2]
    finally:
        watcher.stop()
    assert watcher._observers == {} and watcher._timers == {}
