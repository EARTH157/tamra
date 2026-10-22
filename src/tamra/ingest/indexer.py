"""Keep every project's index in step with its folder: reconcile, then index one file at a time."""

import logging
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import numpy as np

from tamra.download import sha256_file
from tamra.ingest.chunker import TokenSpans, chunk_document
from tamra.ingest.parsers import SUPPORTED, ParseError, parse_file, with_ocr
from tamra.ocr import OcrPageResult, render_page
from tamra.store import OcrPage, Store

log = logging.getLogger(__name__)

EMBED_BATCH = 16  # texts per embedder call; stop() is honored between calls
MAX_FILE_BYTES = 200 * 1024 * 1024  # larger files are skipped, not parsed into memory


class EmbedderLike(Protocol):
    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray: ...


class OcrLike(Protocol):
    def read(self, image: np.ndarray) -> OcrPageResult: ...


@dataclass(frozen=True)
class IndexerState:
    current: str | None  # file of this project being indexed right now
    error: str | None  # what stops indexing it (missing folder, unavailable model)
    reading: tuple[int, int] | None = None  # (n, of): OCR is reading the n-th of `of` pages


class _Pause(Exception):
    """Indexing cannot continue until something outside the documents changes."""


def scan_folder(folder: Path) -> tuple[dict[str, tuple[int, float]], list[str]]:
    """Supported documents under folder, and the places that could not be read.

    The documents map a forward-slash relative path to (size, mtime). The second item lists the
    relative paths of folders that could not be listed and files that could not be stat'ed ("."
    when the location is unknown): what is under them is unknown, not gone, so reconcile must
    not delete it.

    Skips dot-files and dot-folders, Office lock files (~$*), hidden or system items, and
    symlinks and directory junctions (a link may point outside the folder or back into it).
    """
    found: dict[str, tuple[int, float]] = {}
    failed: list[str] = []

    def unreadable(location: object) -> None:
        try:
            failed.append(Path(str(location)).relative_to(folder).as_posix())
        except ValueError:
            failed.append(".")

    for root, dirs, names in os.walk(
        folder, followlinks=False, onerror=lambda e: unreadable(e.filename)
    ):
        dirs[:] = [d for d in dirs if not _ignored(Path(root, d)) and not _is_link(Path(root, d))]
        for name in names:
            path = Path(root, name)
            if path.suffix.lower() not in SUPPORTED or _ignored(path) or _is_link(path):
                continue
            try:
                info = path.stat()
            except OSError:
                unreadable(path)
                continue
            found[path.relative_to(folder).as_posix()] = (info.st_size, info.st_mtime)
    return found, failed


def _under(rel_path: str, places: list[str]) -> bool:
    rel = rel_path.casefold()  # Windows paths compare without case
    return any(
        p == "." or rel == p.casefold() or rel.startswith(p.casefold() + "/") for p in places
    )


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
        *,
        ocr: Callable[[], OcrLike | None] = lambda: None,
    ):
        self._store = store
        self._ocr = ocr
        self._reading: tuple[int, int] | None = None
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
                self._current[1] if working else None,
                self._errors.get(collection_id),
                self._reading if working else None,
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
        on_disk, failed = scan_folder(folder)
        known = {record.rel_path: record for record in self._store.list_files(collection_id)}
        for rel_path, record in known.items():
            if rel_path not in on_disk and not _under(rel_path, failed):
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
        if failed:  # the rest of the project is still reconciled and indexed
            where = "the project folder" if failed[0] == "." else failed[0]
            self._set_error(collection_id, f"Could not read: {where}")

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
            if path.stat().st_size > MAX_FILE_BYTES:
                self._store.set_file_status(file_id, "skipped", "file too large (over 200 MB)")
                return
            try:
                embedder = self._embedder()
                spans = self._token_spans()
            except Exception as e:
                self._store.set_file_status(file_id, "pending")
                raise _Pause(f"Embedding model unavailable: {e}") from e
            content_hash = sha256_file(path)
            doc = parse_file(path)
            pages: list[OcrPage] = []
            needs_ocr = False
            if doc.ocr_pages:
                engine = self._ocr()
                if engine is None:  # not installed: the pages wait, the rest is indexed
                    needs_ocr = True
                else:
                    read = self._read_pages(file_id, path, doc.ocr_pages, engine)
                    if read is None:
                        return
                    texts, pages, failed = read
                    note = (
                        "OCR failed on page(s) " + ", ".join(map(str, failed)) if failed else None
                    )
                    doc = replace(with_ocr(doc, texts), note=note)
            chunks = chunk_document(doc, spans)
            if not chunks:
                self._store.set_file_status(
                    file_id, "skipped", doc.note or "no text found", needs_ocr=needs_ocr
                )
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
                    file_id,
                    chunks,
                    vectors,
                    content_hash=content_hash,
                    note=doc.note,
                    ocr_pages=pages,
                    needs_ocr=needs_ocr,
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

    def _read_pages(
        self, file_id: int, path: Path, pages: tuple[int, ...], engine: OcrLike
    ) -> tuple[dict[int, str], list[OcrPage], list[int]] | None:
        """OCR the pages one at a time: (page -> text, rows to store, pages that failed). None
        when indexing must stop between pages: on shutdown (the file goes back to pending) or
        when the file left the index (its project was deleted, or the file was removed)."""
        texts: dict[int, str] = {}
        rows: list[OcrPage] = []
        failed: list[int] = []
        try:
            for n, page in enumerate(pages, start=1):
                if self._stopping.is_set():
                    self._store.set_file_status(file_id, "pending")
                    return None
                if self._store.get_file(file_id) is None:
                    return None
                with self._lock:
                    self._reading = (n, len(pages))
                try:
                    result = engine.read(render_page(path, page))  # PDFium is held only to draw
                except Exception:  # one unreadable page must not lose the file
                    log.warning("OCR of page %s of %s failed", page, path.name, exc_info=True)
                    failed.append(page)
                    continue
                texts[page] = result.text
                if result.text:
                    boxes = np.asarray(result.boxes, dtype="<f4").tobytes()
                    rows.append(OcrPage(page, result.text, boxes))
        finally:
            with self._lock:
                self._reading = None
        return texts, rows, failed


def _hash_or_none(path: Path) -> str | None:
    try:
        return sha256_file(path)
    except OSError:
        return None
