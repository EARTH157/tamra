"""Watch project folders and report each project's changes once it has been quiet for a moment."""

import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


class FolderWatcher:
    def __init__(self, on_change: Callable[[int], None], debounce: float = 2.0):
        """on_change(collection_id) is called once a project's folder has been quiet for
        `debounce` seconds after a change."""
        self._on_change = on_change
        self._debounce = debounce
        self._lock = threading.Lock()
        self._timers: dict[int, threading.Timer] = {}
        self._observers: dict[int, Observer] = {}

    def watch(self, collection_id: int, folder: Path) -> None:
        """Watch folder (recursively) for this project instead of any folder it had before."""
        self.unwatch(collection_id)
        observer = Observer()
        observer.schedule(
            _Handler(lambda: self._poke(collection_id, observer)), str(folder), recursive=True
        )
        observer.daemon = True
        with self._lock:
            # Set before start() so an event dispatched right away is not dropped by _poke.
            self._observers[collection_id] = observer
        try:
            observer.start()
        except BaseException:
            with self._lock:
                if self._observers.get(collection_id) is observer:
                    del self._observers[collection_id]
            raise

    def unwatch(self, collection_id: int) -> None:
        """Stop watching a project; a change already seen but not yet reported is dropped."""
        with self._lock:
            observer = self._observers.pop(collection_id, None)
            timer = self._timers.pop(collection_id, None)
        if timer is not None:
            timer.cancel()
        if observer is not None:
            observer.stop()
            if observer.is_alive():  # one that a racing watch() has not started cannot be joined
                observer.join(timeout=5)

    def stop(self) -> None:
        """Stop watching every project."""
        with self._lock:
            ids = list(self._observers)
        for collection_id in ids:
            self.unwatch(collection_id)

    def _poke(self, collection_id: int, observer: Observer) -> None:
        with self._lock:
            if self._observers.get(collection_id) is not observer:
                return  # replaced or stopped: a late event from an old observer
            if (previous := self._timers.get(collection_id)) is not None:
                previous.cancel()
            timer = threading.Timer(self._debounce, self._fire, args=(collection_id,))
            timer.daemon = True
            self._timers[collection_id] = timer
            timer.start()

    def _fire(self, collection_id: int) -> None:
        with self._lock:
            # Timer.cancel() cannot stop a timer that already finished waiting, so only the
            # project's current timer may fire; a replaced or stopped one is stale.
            if self._timers.get(collection_id) is not threading.current_thread():
                return
            del self._timers[collection_id]
        self._on_change(collection_id)


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
