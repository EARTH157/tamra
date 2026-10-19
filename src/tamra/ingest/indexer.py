"""Keep every project's index in step with its folder: reconcile, then index one file at a time."""

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

EMBED_BATCH = 16  # texts per embedder call; stop() is honored between calls
MAX_FILE_BYTES = 200 * 1024 * 1024  # larger files are skipped, not parsed into memory


class EmbedderLike(Protocol):
    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray: ...


@dataclass(frozen=True)
class IndexerState:
    current: str | None  # file of this project being indexed right now
    error: str | None  # what stops indexing it (missing folder, unavailable model)


class _Pause(Exception):
    """Indexing cannot continue until something outside the documents changes."""


def scan_folder(folder: Path) -> dict[str, tuple[int, float]]:
    """Supported documents under folder: forward-slash relative path -> (size, mtime).

    Skips dot-files and dot-folders, Office lock files (~$*), hidden or system items, and
    symlinks and directory junctions (a link may point outside the folder or back into it).

    Raises OSError when a folder cannot be listed: it must not pass for empty, or reconcile
    would delete its files.
    """
    found: dict[str, tuple[int, float]] = {}

    def fail(error: OSError) -> None:
        raise error  # an unreadable folder must not look empty: reconcile would delete its files

    for root, dirs, names in os.walk(folder, followlinks=False, onerror=fail):
        dirs[:] = [d for d in dirs if not _ignored(Path(root, d)) and not _is_link(Path(root, d))]
        for name in names:
            path = Path(root, name)
            if path.suffix.lower() not in SUPPORTED or _ignored(path) or _is_link(path):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            found[path.relative_to(folder).as_posix()] = (info.st_size, info.st_mtime)
    return found


def _is_link(path: Path) -> bool:
    return path.is_symlink() or os.path.isjunction(path)


def _ignored(path: Path) -> bool:
    if path.name.startswith((".", "~$")):
        return True
    try:
        attributes = path.stat().st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


class Indexer:
    """The single background worker of spec §5, shared by every project.

    Requests are coalesced into passes: a pass reconciles the requested projects, then indexes
    pending files, oldest first across all projects.
    """

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
        self._reconcile_all = False
        self._reconcile: set[int] = set()
        self._rebuild: set[int] = set()
        self._current: tuple[int, str] | None = None  # (project, file) being indexed now
        self._errors: dict[int, str] = {}
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

    def request_reconcile(self, collection_id: int | None = None) -> None:
        """Compare a project's folder with its index; None means every project."""
        with self._lock:
            if collection_id is None:
                self._reconcile_all = True
            else:
                self._reconcile.add(collection_id)
        self._wake.set()

    def request_rebuild(self, collection_id: int) -> None:
        with self._lock:
            self._rebuild.add(collection_id)
            self._reconcile.add(collection_id)
        self._wake.set()

    def forget(self, collection_id: int) -> None:
        """Drop what is remembered about a project that was deleted."""
        with self._lock:
            self._reconcile.discard(collection_id)
            self._rebuild.discard(collection_id)
            self._errors.pop(collection_id, None)

    def state(self, collection_id: int) -> IndexerState:
        """What the worker is doing for this project. It reads memory only, no store queries."""
        with self._lock:
            working = self._current is not None and self._current[0] == collection_id
            return IndexerState(
                self._current[1] if working else None, self._errors.get(collection_id)
            )

    def process(self) -> None:
        """Handle the pending requests and index pending files, on the calling thread."""
        with self._lock:
            reconcile_all, reconcile, rebuild = self._reconcile_all, self._reconcile, self._rebuild
            self._reconcile_all, self._reconcile, self._rebuild = False, set(), set()
        blocked: list[int] = []  # projects whose files cannot be indexed in this pass
        for collection in self._store.list_collections():
            if self._stopping.is_set():
                return
            if collection.id in rebuild:
                self._store.reset_index(collection.id, self._model_id)
                collection = self._store.get_collection(collection.id)
                if collection is None:
                    continue
            if collection.embedding_model_id != self._model_id:
                self._set_error(collection.id, None)  # nothing to do; a stale project says so
                continue
            folder = Path(collection.folder_path)
            if not folder.is_dir():
                self._set_error(collection.id, f"Folder not found: {folder}")
                blocked.append(collection.id)
                continue
            self._set_error(collection.id, None)
            if reconcile_all or collection.id in reconcile:
                try:
                    self._reconcile_project(collection.id, folder)
                except Exception as e:  # one project must not stop the others
                    if self._store.get_collection(collection.id) is None:
                        continue  # deleted while it was reconciled: its failure is moot
                    log.exception("reconciling project %s failed", collection.id)
                    self._set_error(collection.id, f"{type(e).__name__}: {e}")
                    blocked.append(collection.id)
        try:
            self._index_pending(blocked)
        except _Pause as pause:
            self._report_pause(str(pause), blocked)

    def _report_pause(self, message: str, blocked: list[int]) -> None:
        """Tell every project that is waiting to be indexed why it is not."""
        for collection in self._store.list_collections():
            if collection.id in blocked or collection.embedding_model_id != self._model_id:
                continue
            if self._store.status_counts(collection.id)["pending"]:
                self._set_error(collection.id, message)

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
                try:
                    for collection in self._store.list_collections():
                        self._set_error(collection.id, f"{type(e).__name__}: {e}")
                except Exception:  # the store itself failed; the log has it
                    log.exception("indexer cannot report the failure")

    def _set_error(self, collection_id: int, error: str | None) -> None:
        with self._lock:
            if error is None:
                self._errors.pop(collection_id, None)
            else:
                self._errors[collection_id] = error

    def _token_spans(self) -> TokenSpans:
        if self._spans is None:
            self._spans = self._token_spans_factory()
        return self._spans

    def _reconcile_project(self, collection_id: int, folder: Path) -> None:
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

    def _index_pending(self, blocked: list[int]) -> None:
        while not self._stopping.is_set():
            with self._lock:
                if self._reconcile_all or self._reconcile or self._rebuild:
                    self._wake.set()  # a newer request wins; the worker loops back to it
                    return
            record = self._store.next_pending_file_any(self._model_id, blocked)
            if record is None:
                return
            collection = self._store.get_collection(record.collection_id)
            if collection is None:  # deleted since the file was picked
                continue
            folder = Path(collection.folder_path)
            if not folder.is_dir():  # e.g. the drive went away during the pass
                self._set_error(collection.id, f"Folder not found: {folder}")
                blocked.append(collection.id)
                continue
            self._index_file(collection.id, record.id, record.rel_path, folder / record.rel_path)

    def _index_file(self, collection_id: int, file_id: int, rel_path: str, path: Path) -> None:
        self._store.set_file_status(file_id, "indexing")
        with self._lock:
            self._current = (collection_id, rel_path)
        try:
            try:
                embedder = self._embedder()
                spans = self._token_spans()
            except Exception as e:
                self._store.set_file_status(file_id, "pending")
                raise _Pause(f"Embedding model unavailable: {e}") from e
            if path.stat().st_size > MAX_FILE_BYTES:
                self._store.set_file_status(file_id, "skipped", "file too large (over 200 MB)")
                return
            content_hash = sha256_file(path)
            doc = parse_file(path)
            chunks = chunk_document(doc, spans)
            if not chunks:
                self._store.set_file_status(file_id, "skipped", doc.note or "no text found")
                return
            texts = [chunk.text for chunk in chunks]
            batches = []
            for start in range(0, len(texts), EMBED_BATCH):
                if self._stopping.is_set():  # shutting down: leave the file for the next start
                    self._store.set_file_status(file_id, "pending")
                    return
                batches.append(embedder.embed(texts[start : start + EMBED_BATCH]))
            vectors = np.concatenate(batches)
            try:
                self._store.replace_file_chunks(
                    file_id, chunks, vectors, content_hash=content_hash, note=doc.note
                )
            except LookupError:
                return  # the file left the project while it was being indexed
        except _Pause:
            raise
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
