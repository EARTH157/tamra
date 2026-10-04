"""Model downloads (one at a time, on a worker thread) and offline import of model files."""

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from tamra.download import DownloadCancelled, download_resumable, sha256_file
from tamra.models.catalog import Catalog, ModelEntry, ModelFile

log = logging.getLogger(__name__)

UNCATALOGUED_WARNING = "This model is not in Tamra's catalog; answer quality is unknown."

_COPY_BLOCK = 1 << 20


class DownloadBusy(RuntimeError):
    """A download is already running; only one runs at a time."""


class ImportRefused(ValueError):
    """The file cannot be imported. The message says why and is safe to show to the user."""


@dataclass(frozen=True)
class ImportResult:
    id: str  # catalog id, or the file's stem for an uncatalogued model
    path: Path  # where the file now lives, inside the models directory
    catalogued: bool
    warning: str | None = None


def _idle() -> dict[str, Any]:
    return {"state": "idle", "done": 0, "total": 0, "error": None}


class ModelManager:
    def __init__(
        self,
        models_dir: Path,
        catalog: Catalog,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.models_dir = models_dir
        self.catalog = catalog
        self._transport = transport  # tests inject httpx.MockTransport
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = {m.id: _idle() for m in catalog.models}
        self._active: str | None = None
        self._cancel = threading.Event()

    # --- downloads ---

    def start_download(self, model_id: str) -> None:
        """Download every file of a catalog entry; raises DownloadBusy if one is running."""
        entry = self.catalog.get(model_id)  # KeyError for an unknown id
        with self._lock:
            if self._active is not None:
                raise DownloadBusy(f"{self._active} is already downloading")
            self._active = model_id
            self._cancel = threading.Event()
            total = sum(f.size for f in entry.files)
            self._state[model_id] = {
                "state": "downloading",
                "done": 0,
                "total": total,
                "error": None,
            }
            cancel = self._cancel
        threading.Thread(
            target=self._run_download,
            args=(entry, cancel),
            name=f"tamra-download-{model_id}",
            daemon=True,
        ).start()

    def cancel_download(self, model_id: str) -> None:
        """Stop a running download, keeping its .part files. Does nothing if it is not running."""
        with self._lock:
            if self._active == model_id:
                self._cancel.set()

    def status(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {model_id: dict(state) for model_id, state in self._state.items()}

    def _set(self, model_id: str, **fields: Any) -> None:
        with self._lock:
            self._state[model_id].update(fields)

    def _run_download(self, entry: ModelEntry, cancel: threading.Event) -> None:
        model_id = entry.id
        finished = 0  # bytes of the files already completed
        try:
            for f in entry.files:

                def on_progress(
                    done: int, total: int, f: ModelFile = f, base: int = finished
                ) -> None:
                    state = "verifying" if done >= f.size else "downloading"
                    self._set(model_id, state=state, done=base + done)

                download_resumable(
                    f.url,
                    self.models_dir / f.path,
                    f.sha256,
                    size=f.size,
                    on_progress=on_progress,
                    cancel=cancel,
                    transport=self._transport,
                )
                finished += f.size
            self._finish(model_id, state="idle", done=finished, error=None)
        except DownloadCancelled:
            self._finish(model_id, state="idle", error=None)
        except Exception as exc:  # the worker must never die silently
            log.warning("download of %s failed: %s", model_id, exc)
            self._finish(model_id, state="error", error=str(exc) or type(exc).__name__)

    def _finish(self, model_id: str, **fields: Any) -> None:
        with self._lock:
            self._state[model_id].update(fields)
            self._active = None

    # --- import ---

    def import_file(self, path: Path) -> ImportResult:
        """Copy a model file into the models directory.

        A file whose sha256 matches a catalog file is stored under that file's catalog path. Any
        other .gguf is copied as it is, with a warning. Everything else is refused. The copy goes
        to a .part file first, so a half-copied file never looks installed.
        """
        path = Path(path)
        if not path.is_file():
            raise ImportRefused(f"{path} is not a file")
        digest = sha256_file(path)
        match = self._find_by_hash(digest)
        if match is not None:
            entry, f = match
            dest = self.models_dir / f.path
            if not self._already_there(dest, f.size, digest):
                self._copy(path, dest, expected=digest)
            return ImportResult(id=entry.id, path=dest, catalogued=True)

        if path.suffix.lower() != ".gguf":
            raise ImportRefused("Only GGUF model files can be imported.")
        clash = self._find_by_name(path.name)
        if clash is not None:
            entry, _ = clash
            raise ImportRefused(
                f"{path.name} has the name of {entry.name} in Tamra's catalog but different "
                "content, so it is probably incomplete or damaged."
            )
        dest = self.models_dir / path.name
        self._copy(path, dest, expected=None)
        return ImportResult(id=path.stem, path=dest, catalogued=False, warning=UNCATALOGUED_WARNING)

    def _find_by_hash(self, digest: str) -> tuple[ModelEntry, ModelFile] | None:
        for entry in self.catalog.models:
            for f in entry.files:
                if f.sha256.lower() == digest:
                    return entry, f
        return None

    def _find_by_name(self, name: str) -> tuple[ModelEntry, ModelFile] | None:
        """A catalog file with this base name. A file with such a name but other content is
        refused rather than stored as an 'uncatalogued' model: it must not replace, or pass for,
        a pinned file (embedding files in particular have to match exactly)."""
        for entry in self.catalog.models:
            for f in entry.files:
                if Path(f.path).name.lower() == name.lower():
                    return entry, f
        return None

    @staticmethod
    def _already_there(dest: Path, size: int, digest: str) -> bool:
        """True when dest already holds this file; it is then left alone (it may be in use)."""
        try:
            return dest.is_file() and dest.stat().st_size == size and sha256_file(dest) == digest
        except OSError:
            return False

    def _copy(self, src: Path, dest: Path, expected: str | None) -> None:
        """Copy src to dest through dest.part. With `expected`, the copied bytes must hash to it."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        h = hashlib.sha256()
        try:
            with src.open("rb") as r, part.open("wb") as w:
                for block in iter(lambda: r.read(_COPY_BLOCK), b""):
                    w.write(block)
                    h.update(block)
            if expected is not None and h.hexdigest() != expected:
                raise ImportRefused(
                    "The file changed while it was being copied; it did not verify."
                )
            os.replace(part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
