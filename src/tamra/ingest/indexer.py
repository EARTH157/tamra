"""Keep a collection's index in step with its folder: reconcile, then index one file at a time."""

import logging
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from tamra.download import sha256_file
from tamra.ingest.chunker import TokenSpans, chunk_document
from tamra.ingest.parsers import SUPPORTED, ParseError, parse_file
from tamra.store import Store

log = logging.getLogger(__name__)


class EmbedderLike(Protocol):
    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray: ...


@dataclass(frozen=True)
class IndexerState:
    current: str | None  # file being indexed right now
    error: str | None  # what stops indexing (missing folder, unavailable model)


class _Pause(Exception):
    """Indexing cannot continue until something outside the documents changes."""


def scan_folder(folder: Path) -> dict[str, tuple[int, float]]:
    """Supported documents under folder: forward-slash relative path -> (size, mtime).

    Skips dot-files and dot-folders, Office lock files (~$*), and hidden or system items.
    """
    found: dict[str, tuple[int, float]] = {}
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not _ignored(Path(root, d))]
        for name in names:
            path = Path(root, name)
            if path.suffix.lower() not in SUPPORTED or _ignored(path):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            found[path.relative_to(folder).as_posix()] = (info.st_size, info.st_mtime)
    return found


def _ignored(path: Path) -> bool:
    if path.name.startswith((".", "~$")):
        return True
    try:
        attributes = path.stat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


class Indexer:
    """The single background worker of spec §5. Requests are coalesced into passes."""

    def __init__(
        self,
        store: Store,
        embedder: Callable[[], EmbedderLike],
        token_spans: Callable[[], TokenSpans],
        model_id: str,
    ):
        self._store = store
        self._embedder = embedder
        self._token_spans_factory = token_spans
        self._spans: TokenSpans | None = None
        self._model_id = model_id
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._reconcile_requested = False
        self._rebuild_requested = False
        self._current: str | None = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="tamra-indexer", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def request_reconcile(self) -> None:
        with self._lock:
            self._reconcile_requested = True
        self._wake.set()

    def request_rebuild(self) -> None:
        with self._lock:
            self._rebuild_requested = True
            self._reconcile_requested = True
        self._wake.set()

    def state(self) -> IndexerState:
        with self._lock:
            return IndexerState(self._current, self._error)

    def process(self) -> None:
        """Handle the pending requests and index pending files, on the calling thread."""
        with self._lock:
            rebuild, reconcile = self._rebuild_requested, self._reconcile_requested
            self._rebuild_requested = self._reconcile_requested = False
        collection = self._store.get_collection()
        if collection is not None and rebuild:
            self._store.reset_index(collection.id, self._model_id)
            collection = self._store.get_collection()
        if collection is None or collection.embedding_model_id != self._model_id:
            self._set_error(None)  # nothing to do; a stale collection reports itself
            return
        folder = Path(collection.folder_path)
        if not folder.is_dir():
            self._set_error(f"Folder not found: {folder}")
            return
        if reconcile:
            self._reconcile(collection.id, folder)
        self._set_error(None)
        try:
            self._index_pending(collection.id, folder)
        except _Pause as pause:
            self._set_error(str(pause))

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stopping.is_set():
                return
            try:
                self.process()
            except Exception as e:  # keep the worker alive; the state says what went wrong
                log.exception("indexer pass failed")
                self._set_error(f"{type(e).__name__}: {e}")

    def _set_error(self, error: str | None) -> None:
        with self._lock:
            self._error = error

    def _token_spans(self) -> TokenSpans:
        if self._spans is None:
            self._spans = self._token_spans_factory()
        return self._spans

    def _reconcile(self, collection_id: int, folder: Path) -> None:
        on_disk = scan_folder(folder)
        known = {record.rel_path: record for record in self._store.list_files(collection_id)}
        for rel_path, record in known.items():
            if rel_path not in on_disk:
                self._store.delete_file(record.id)
        for rel_path, (size, mtime) in on_disk.items():
            record = known.get(rel_path)
            if record is None:
                self._store.add_file(collection_id, rel_path, size, mtime)
            elif record.status == "indexing":  # interrupted by a crash or shutdown
                self._store.update_file_stat(record.id, size, mtime, pending=True)
            elif (size, mtime) != (record.size, record.mtime):
                changed = (
                    record.content_hash is None
                    or _hash_or_none(folder / rel_path) != record.content_hash
                )
                self._store.update_file_stat(record.id, size, mtime, pending=changed)

    def _index_pending(self, collection_id: int, folder: Path) -> None:
        while not self._stopping.is_set():
            with self._lock:
                if self._reconcile_requested or self._rebuild_requested:
                    self._wake.set()  # a newer request wins; the worker loops back to it
                    return
            record = self._store.next_pending_file(collection_id)
            if record is None:
                return
            self._index_file(record.id, record.rel_path, folder / record.rel_path)

    def _index_file(self, file_id: int, rel_path: str, path: Path) -> None:
        self._store.set_file_status(file_id, "indexing")
        with self._lock:
            self._current = rel_path
        try:
            try:
                embedder = self._embedder()
                spans = self._token_spans()
            except Exception as e:
                self._store.set_file_status(file_id, "pending")
                raise _Pause(f"Embedding model unavailable: {e}") from e
            content_hash = sha256_file(path)
            doc = parse_file(path)
            chunks = chunk_document(doc, spans)
            if not chunks:
                self._store.set_file_status(file_id, "skipped", doc.note or "no text found")
                return
            vectors = embedder.embed([chunk.text for chunk in chunks])
            self._store.replace_file_chunks(
                file_id, chunks, vectors, content_hash=content_hash, note=doc.note
            )
        except _Pause:
            raise
        except LookupError:
            pass  # the file left the collection while it was being indexed
        except (ParseError, OSError) as e:
            self._store.set_file_status(file_id, "failed", str(e))
        except Exception as e:  # one bad file must not stop the others
            log.exception("indexing %s failed", rel_path)
            self._store.set_file_status(file_id, "failed", f"{type(e).__name__}: {e}")
        finally:
            with self._lock:
                self._current = None


def _hash_or_none(path: Path) -> str | None:
    try:
        return sha256_file(path)
    except OSError:
        return None
