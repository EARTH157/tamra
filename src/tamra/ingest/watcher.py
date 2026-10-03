"""Watch a collection folder and report changes once it has been quiet for a moment."""

import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


class FolderWatcher:
    def __init__(self, on_change: Callable[[], None], debounce: float = 2.0):
        self._on_change = on_change
        self._debounce = debounce
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._observer = None

    def watch(self, folder: Path) -> None:
        """Watch folder (recursively) instead of any folder watched before."""
        self.stop()
        observer = Observer()
        observer.schedule(_Handler(self._poke), str(folder), recursive=True)
        observer.daemon = True
        observer.start()
        with self._lock:
            self._observer = observer

    def stop(self) -> None:
        with self._lock:
            observer, self._observer = self._observer, None
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)

    def _poke(self) -> None:
        with self._lock:
            if self._observer is None:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._timer is None:
                return
            self._timer = None
        self._on_change()


class _Handler(FileSystemEventHandler):
    def __init__(self, poke: Callable[[], None]):
        self._poke = poke

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in ("created", "modified", "deleted", "moved"):
            return  # open/close notifications do not change content
        if event.is_directory and event.event_type == "modified":
            return  # a folder's own timestamp; the file events inside it are what matter
        names = [Path(str(p)).name for p in (event.src_path, getattr(event, "dest_path", "")) if p]
        if names and all(name.startswith("~$") for name in names):
            return  # Office lock files
        self._poke()
