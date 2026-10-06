"""Core: owns the store, the indexer and watcher, the answer service, and the LLM providers."""

import hashlib
import logging
import os
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from tamra import secrets
from tamra.answer import AnswerService, AnswerSettings, Route
from tamra.attribution import Attributor, Match
from tamra.embedder import MODEL_ID, Embedder
from tamra.ingest.chunker import TokenSpans, bge_token_spans
from tamra.ingest.indexer import EmbedderLike, Indexer
from tamra.ingest.parsers import ParsedDoc, ParseError, parse_file
from tamra.ingest.watcher import FolderWatcher
from tamra.llm.anthropic_api import AnthropicLLM
from tamra.llm.base import Provider, ProviderError
from tamra.llm.openai_compat import OpenAICompatibleLLM
from tamra.llm.runtime import LocalLLM
from tamra.models.catalog import Catalog, load_catalog
from tamra.models.hardware import Hardware, detect
from tamra.models.manager import ModelManager
from tamra.settings import Settings, load_settings, save_settings
from tamra.store import Collection, FileRecord, MessageRecord, SourceRecord, Store
from tamra.viewer import FileMissing, Unreadable, ViewerError, resolve_file

log = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com"
# Changing one of these swaps the provider. local_model_id does not: LocalLLM reads the model
# path on every question and restarts llama-server on the new model by itself.
_PROVIDER_FIELDS = frozenset({"mode", "api_provider", "api_model", "api_base_url"})
DOCUMENT_CACHE_SIZE = 8  # parsed documents kept for the viewer, so paging a PDF parses it once
DOCUMENT_CACHE_MAX_BYTES = 16 * 1024 * 1024  # a larger file is parsed per request, not kept


class EmbedderUnavailable(RuntimeError):
    """The embedding model could not be loaded."""


class AnswerBusy(RuntimeError):
    """An answer is being written, so a project cannot be deleted right now."""


class BadName(ValueError):
    """A project name that is empty, too long, or has control or format characters."""


class FolderUsed(ValueError):
    """The folder is, or lies inside or around, another project's folder."""


class FolderMissing(ValueError):
    """The path is not an absolute, existing directory."""


class FolderNotAllowed(ValueError):
    """A whole drive or share, or a folder that holds or lies inside Tamra's own folders."""


MAX_PROJECT_NAME = 80
_HIDDEN = frozenset({"Cc", "Cf"})  # control and format characters, bidi overrides among them


def _clean_name(name: str) -> str:
    """A project name as it is stored: whitespace collapsed, 1 to 80 characters, with no control
    or format characters (BadName otherwise). Given and derived names both pass through it."""
    cleaned = " ".join(name.split())
    if not 1 <= len(cleaned) <= MAX_PROJECT_NAME:
        raise BadName("bad name length")
    if any(unicodedata.category(c) in _HIDDEN for c in cleaned):
        raise BadName("bad name characters")
    return cleaned


def _derived_name(path: Path) -> str:
    """The name for a project made without one: its folder's name, made fit to be a name."""
    raw = path.name or str(path)
    visible = "".join(c for c in raw if unicodedata.category(c) not in _HIDDEN)
    cut = " ".join(visible.split())[:MAX_PROJECT_NAME].rstrip()
    return _clean_name(cut or "Project")


def _overlaps(first: Path, second: Path) -> bool:
    """Whether two folders are the same or one lies inside the other (Windows ignores case)."""
    a, b = (Path(os.path.normcase(p.resolve())) for p in (first, second))
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _build_api_provider(settings: Settings, key: str) -> Provider:
    if settings.api_provider == "anthropic":
        return AnthropicLLM(settings.api_model, key)
    return OpenAICompatibleLLM(
        base_url=settings.api_base_url or OPENAI_BASE_URL,
        model=settings.api_model,
        api_key=key,
        kind="api",
    )


class Core:
    def __init__(
        self,
        data_dir: Path,
        models_dir: Path,
        llama_exe: Path,
        *,
        embedder_factory: Callable[[], EmbedderLike] | None = None,
        token_spans_factory: Callable[[], TokenSpans] | None = None,
        llm: LocalLLM | None = None,
        api_factory: Callable[[Settings, str], Provider] = _build_api_provider,
        catalog: Catalog | None = None,
        answer_settings: AnswerSettings | None = None,
        debounce: float = 2.0,
        warm_attribution: bool = False,
    ):
        """`llm` replaces the local runtime and `api_factory` builds the API provider from the
        settings and the key; both exist so tests can use fakes. `warm_attribution` embeds a
        new answer's sources in the background so the first "Check source" is fast."""
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        bge = models_dir / "bge-m3"
        self.store = Store.open(data_dir / "tamra.db")
        self._embedder_factory = embedder_factory or (lambda: Embedder.load(bge))
        self._embedder: EmbedderLike | None = None
        self._embedder_lock = threading.Lock()
        spans_factory = token_spans_factory or (lambda: bge_token_spans(bge / "tokenizer.json"))
        self._spans_factory = spans_factory
        self._attributor: Attributor | None = None
        self._attributor_lock = threading.Lock()
        self._warm_pool = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="tamra-attribution-warm")
            if warm_attribution
            else None
        )
        self._closed = False
        self._projects_lock = threading.Lock()  # guards the folder checks of project changes
        self._documents: OrderedDict[tuple, ParsedDoc] = OrderedDict()
        self._documents_lock = threading.Lock()
        self._models_dir = models_dir
        self._llama_exe = llama_exe
        self._catalog = catalog or load_catalog()
        self.models = ModelManager(models_dir, self._catalog)
        self._hardware: Hardware | None = None
        self._hardware_lock = threading.Lock()
        self._settings = load_settings(self.store)
        self._provider_lock = threading.Lock()  # guards the settings swap and the API cache
        self._api_factory = api_factory
        self._api: Provider | None = None  # built on the first API answer, not at startup
        self._api_identity: tuple | None = None  # what _api was built from (the key as a sha256)
        self.local = llm or LocalLLM(
            llama_exe,
            self._local_model_path,
            data_dir / "logs" / "llama-server.log",
            label_for=self._local_label,
        )
        self.indexer = Indexer(self.store, self.embedder, spans_factory, MODEL_ID)
        self.watcher = FolderWatcher(self.indexer.request_reconcile, debounce=debounce)
        self.answers = AnswerService(
            self.store,
            self._embed_query,
            self._route,
            model_id=MODEL_ID,
            settings=answer_settings,
            on_saved=self._warm if warm_attribution else None,
        )

    # --- settings and providers ---

    @property
    def settings(self) -> Settings:
        return self._settings

    def apply_settings(self, changes: dict[str, object]) -> Settings:
        """Validate and save the changes (ValueError if any is bad), then swap providers.

        An answer may be streaming on another thread, so a provider that is being replaced is
        closed only once that answer has ended (AnswerService.when_idle). The next question
        builds what it needs: nothing here starts llama-server or reads the API key.
        """
        with self._provider_lock:
            before = self._settings
            settings = save_settings(self.store, changes)
            self._settings = settings
            # Compare values: a client may send the whole form with only one field changed.
            swapped = any(getattr(before, f) != getattr(settings, f) for f in _PROVIDER_FIELDS)
            retired_api = None
            if swapped:
                retired_api, self._api, self._api_identity = self._api, None, None
        if retired_api is not None:
            self.answers.when_idle(retired_api.close)
        if swapped and settings.mode == "api":
            self.answers.when_idle(self._close_local_if_unused)  # free the model's memory
        return settings

    def _close_local_if_unused(self) -> None:
        # Runs after an answer ends, so the mode is read now: if the user switched back to
        # local in the meantime, the server must stay up.
        if self._settings.mode == "api":
            self.local.close()

    def open_api(self) -> Provider:
        """The API provider for the saved settings and the stored key (ProviderError if there is
        no key). It is the one answers use, so the caller must not close it."""
        return self._open_api(self._settings)

    def retire_api(self) -> None:
        """Drop the cached API provider, e.g. after its key was set or removed. It is closed once
        the answer streaming on it (if any) has ended; the next question builds a new one."""
        with self._provider_lock:
            retired, self._api, self._api_identity = self._api, None, None
        if retired is not None:
            self.answers.when_idle(retired.close)

    def hardware(self) -> Hardware:
        """RAM and GPUs, probed on the first call (it runs llama-server) and then remembered."""
        with self._hardware_lock:
            if self._hardware is None:
                self._hardware = detect(self._llama_exe)
            return self._hardware

    def _route(self) -> Route:
        settings = self._settings  # one snapshot: the name and the provider agree
        if settings.mode == "local":
            return Route("local", self.local.client)
        return Route(settings.api_provider, lambda: self._open_api(settings))

    def _open_api(self, settings: Settings) -> Provider:
        """The API provider for these settings, built when first needed.

        The key is read on every call, so a key changed in Settings applies to the next
        question without any signal from the key store.
        """
        try:
            key = secrets.get_api_key(settings.api_provider)
        except Exception as e:  # any keyring backend failure; the key is never in the message
            log.warning("cannot read the API key: %s", type(e).__name__)
            raise ProviderError("The API key could not be read from the system.", "auth") from e
        if not key:
            raise ProviderError("No API key is set.", "auth")
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()  # no plaintext key kept here
        identity = (settings.api_provider, settings.api_model, settings.api_base_url, key_hash)
        with self._provider_lock:
            # `settings` can be older than the cache (they changed after this answer started):
            # the cache is then replaced, and the next answer rebuilds from the current ones.
            if self._api is not None and self._api_identity == identity:
                return self._api
            replaced = self._api
            provider = self._api_factory(settings, key)
            self._api, self._api_identity = provider, identity
        if replaced is not None:
            self.answers.when_idle(replaced.close)
        return provider

    def resolve_local_model(self) -> tuple[str, Path, str]:
        """The local model to serve, as (id, file, label): the selected one if installed, else the
        first installed catalog model, else the first GGUF that is not in the catalog. The id is
        a catalog id or "import:<file name>", so it is a valid `local_model_id`. The label is
        the catalog name ("Qwen3-8B"), or the file name without its extension for an import.

        Raises ProviderError (model_missing) when no model is installed.
        """
        installed = self._catalog.installed(self._models_dir)
        llm_ids = [m.id for m in self._catalog.llms() if m.id in installed]
        uncatalogued = self._catalog.uncatalogued(self._models_dir)
        wanted = self._settings.local_model_id
        if wanted in llm_ids:
            return wanted, installed[wanted], self._catalog.get(wanted).name
        if wanted and wanted.startswith("import:"):
            name = wanted.removeprefix("import:").lower()
            for path in uncatalogued:
                if path.name.lower() == name:
                    return f"import:{path.name}", path, path.stem
        if llm_ids:
            return llm_ids[0], installed[llm_ids[0]], self._catalog.get(llm_ids[0]).name
        if uncatalogued:
            return f"import:{uncatalogued[0].name}", uncatalogued[0], uncatalogued[0].stem
        raise ProviderError("No local model is installed.", "model_missing")

    def _local_model_path(self) -> Path:
        return self.resolve_local_model()[1]

    def _local_label(self, path: Path) -> str:
        """The name to show for the model file `path`: its catalog name, else its file stem."""
        installed = self._catalog.installed(self._models_dir)
        for entry in self._catalog.llms():
            if installed.get(entry.id) == path:
                return entry.name
        return path.stem

    # --- documents ---

    def embedder(self) -> EmbedderLike:
        """The embedding model, loaded on first use and shared by indexing and questions."""
        with self._embedder_lock:
            if self._embedder is None:
                try:
                    self._embedder = self._embedder_factory()
                except Exception as e:  # missing or unreadable model files
                    raise EmbedderUnavailable(str(e)) from e
            return self._embedder

    def _embed_query(self, text: str) -> np.ndarray:
        return self.embedder().embed([text])[0]

    # --- attribution and the viewer ---

    def attribute(
        self,
        message: MessageRecord,
        selection: str,
        only: int | None = None,
        start: int | None = None,
    ) -> list[Match]:
        """Match a selection to the message's sources. It shares the one embedder.

        start: the selection's code-point offset in the message, if the caller knows it.
        """
        return self._attributor_for().attribute(message, selection, only, start)

    def _attributor_for(self) -> Attributor:
        with self._attributor_lock:
            if self._attributor is None:
                try:
                    spans = self._spans_factory()
                except Exception as e:  # missing or unreadable tokenizer file
                    raise EmbedderUnavailable(str(e)) from e
                self._attributor = Attributor(lambda texts: self.embedder().embed(texts), spans)
            return self._attributor

    def _warm(self, message_id: int) -> None:
        """Queue the warm-up of a saved answer on the single background worker."""
        if self._warm_pool is not None and not self._closed:
            try:
                self._warm_pool.submit(self._warm_now, message_id)
            except RuntimeError:  # the pool was shut down meanwhile
                pass

    def _warm_now(self, message_id: int) -> None:
        if self._closed:
            return
        try:
            message = self.store.get_message(message_id)
            if message is not None and message.sources:
                self._attributor_for().prepare(message)
        except Exception as e:  # warming is only an optimisation
            log.warning("attribution warm-up failed: %s", type(e).__name__)

    def file_path(self, file_id: int) -> tuple[FileRecord, Path]:
        """The stored record and the real path of an indexed file (ViewerError if it is gone or
        its stored path leaves the collection folder). The path always comes from the store."""
        record = self.store.get_file(file_id)
        collection = self.store.get_collection(record.collection_id) if record else None
        if collection is None or record is None:
            raise FileMissing("file not found")
        return record, resolve_file(collection.folder_path, record.rel_path)

    def source_changed(self, source: SourceRecord) -> bool:
        """Whether the file no longer matches what the answer was given: it is gone or refused,
        it was re-indexed with other content, or it was edited since it was last indexed."""
        if source.file_id is None:
            return True
        try:
            record, path = self.file_path(source.file_id)
        except ViewerError:
            return True
        return self.file_changed(record, path, source.file_hash)

    @staticmethod
    def file_changed(record: FileRecord, path: Path, file_hash: str | None) -> bool:
        if record.content_hash != file_hash:
            return True
        try:
            stat = path.stat()
        except OSError:
            return True
        # The indexer's own test for "edited": a file that differs here is queued for re-indexing.
        return (stat.st_size, stat.st_mtime) != (record.size, record.mtime)

    def document(self, file_id: int) -> tuple[FileRecord, Path, ParsedDoc]:
        """The file parsed as it is now, from a small cache keyed by size and modified time."""
        record, path = self.file_path(file_id)
        try:
            stat = path.stat()
        except OSError as e:
            raise FileMissing("file not found") from e
        key = (file_id, str(path), stat.st_size, stat.st_mtime_ns)
        with self._documents_lock:
            doc = self._documents.get(key)
            if doc is not None:
                self._documents.move_to_end(key)
                return record, path, doc
        try:
            doc = parse_file(path)
        except FileNotFoundError as e:  # removed between the stat and the parse
            raise FileMissing("file not found") from e
        except (ParseError, OSError) as e:  # damaged, or locked or denied by the system
            raise Unreadable(f"cannot read file: {e}", pdf=path.suffix.lower() == ".pdf") from e
        if stat.st_size > DOCUMENT_CACHE_MAX_BYTES:
            return record, path, doc
        with self._documents_lock:
            self._documents[key] = doc
            while len(self._documents) > DOCUMENT_CACHE_SIZE:
                self._documents.popitem(last=False)
        return record, path, doc

    # --- projects ---

    def start(self) -> None:
        self.indexer.start()
        collections = self.store.list_collections()
        for collection in collections:
            self._watch(collection)
        if collections:
            self.indexer.request_reconcile()

    def projects(self) -> list[dict]:
        """Every project with its index status, ordered by name."""
        return [self._project_view(collection) for collection in self.store.list_collections()]

    def project(self, collection_id: int) -> dict | None:
        """One project as `projects()` lists it; None if it is unknown."""
        collection = self.store.get_collection(collection_id)
        return None if collection is None else self._project_view(collection)

    def _project_view(self, collection: Collection) -> dict:
        return {
            "id": collection.id,
            "name": collection.name,
            "folder_path": collection.folder_path,
            **self._index_status(collection),
        }

    def create_project(self, name: str, folder: str) -> Collection:
        """Add a project and index its folder. ValueError (with a message safe to show) if the
        folder is not usable, is already used by another project, or the name is taken.

        Chats that have no home project (made before any project existed) are adopted by the
        first project that remains, so the first project gives them their home and scope."""
        with self._projects_lock:
            path = self._checked_folder(folder)
            collection = self.store.create_collection(
                _clean_name(name) if name.strip() else _derived_name(path), str(path), MODEL_ID
            )
            if len(self.store.list_collections()) == 1:
                self.store.adopt_homeless_chats(collection.id)
            self._watch(collection)  # under the lock: a delete cannot unwatch before this
        self.indexer.request_reconcile(collection.id)
        return collection

    def rename_project(self, collection_id: int, name: str) -> Collection | None:
        """Rename a project (None if unknown; BadName if the name is not valid, NameTaken if
        another project has it)."""
        if not self.store.rename_collection(collection_id, _clean_name(name)):
            return None
        return self.store.get_collection(collection_id)

    def change_project_folder(self, collection_id: int, folder: str) -> Collection | None:
        """Point a project at another folder: its index is dropped and built again from the new
        one. None if the project is unknown; ValueError as in create_project."""
        if self.store.get_collection(collection_id) is None:
            return None
        with self._projects_lock:
            path = self._checked_folder(folder, excluding=collection_id)
            file_ids = [f.id for f in self.store.list_files(collection_id)]
            collection = self.store.set_collection_folder(collection_id, str(path), MODEL_ID)
            if collection is None:  # deleted meanwhile: there is nothing to watch
                return None
            self._watch(collection)
        self._forget_documents(file_ids)
        self.indexer.request_reconcile(collection.id)
        return collection

    def rebuild_project(self, collection_id: int) -> bool:
        """Index a project again from scratch; False if it is unknown."""
        if self.store.get_collection(collection_id) is None:
            return False
        self.indexer.request_rebuild(collection_id)
        return True

    def delete_project(self, collection_id: int) -> int | None:
        """Delete a project's index and its home chats (the folder is untouched); returns how
        many chats went, or None if the project is unknown. AnswerBusy while an answer streams."""
        outcome: list[int | None] = []
        file_ids: list[int] = []

        def delete() -> None:
            file_ids.extend(f.id for f in self.store.list_files(collection_id))
            outcome.append(self.store.delete_collection(collection_id))

        # The store delete comes first, inside run_if_idle (an answer may be using the project);
        # then the watcher, the indexer's memory and the viewer cache let go of it.
        if not self.answers.run_if_idle(delete):
            raise AnswerBusy("an answer is being written")
        with self._projects_lock:  # a folder change or a create cannot re-watch it meanwhile
            self.watcher.unwatch(collection_id)
            self.indexer.forget(collection_id)
            self._forget_documents(file_ids)
        return outcome[0]

    def _checked_folder(self, folder: str, excluding: int | None = None) -> Path:
        """The resolved folder, if it exists, is not a whole drive or one of Tamra's own folders,
        and does not overlap another project's folder. Each refusal is its own ValueError."""
        path = Path(folder.strip()).expanduser()
        # A device-namespace alias (\\?\C:\..., \\.\...) would slip past the overlap checks,
        # which compare plain paths; the folder picker never returns one.
        if folder.strip().startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
            raise FolderMissing("Choose a full folder path, for example C:\\Users\\me\\Documents.")
        if not path.is_absolute():
            raise FolderMissing("Choose a full folder path, for example C:\\Users\\me\\Documents.")
        if not path.is_dir():
            raise FolderMissing(f"Folder not found: {folder}")
        path = path.resolve()
        if path.parent == path:  # a drive or a share root
            raise FolderNotAllowed("a whole drive")
        # Indexing Tamra's data or models would index its own database; a parent of them (such
        # as a profile folder that holds AppData) would do the same.
        if _overlaps(path, self.data_dir) or _overlaps(path, self._models_dir):
            raise FolderNotAllowed("Tamra's own folder")
        for other in self.store.list_collections():
            if other.id != excluding and _overlaps(path, Path(other.folder_path)):
                raise FolderUsed("folder already used")  # its files would be indexed twice
        return path

    def _forget_documents(self, file_ids: list[int]) -> None:
        """Drop the viewer's cached documents of files that left the index."""
        gone = set(file_ids)
        with self._documents_lock:
            for key in [key for key in self._documents if key[0] in gone]:
                del self._documents[key]

    def _index_status(self, collection: Collection) -> dict:
        state = self.indexer.state(collection.id)
        return {
            "stale": collection.embedding_model_id != MODEL_ID,
            "counts": self.store.status_counts(collection.id),
            "current": state.current,
            "error": state.error,
            "problems": [
                {"rel_path": f.rel_path, "status": f.status, "error": f.error}
                for f in self.store.problem_files(collection.id)
            ],
        }

    def shutdown(self) -> None:
        self._closed = True
        if self._warm_pool is not None:
            self._warm_pool.shutdown(wait=False, cancel_futures=True)
        self.watcher.stop()
        self.answers.cancel()
        self.indexer.stop()
        self.local.close()
        with self._provider_lock:
            api, self._api, self._api_identity = self._api, None, None
        if api is not None:
            api.close()
        self.store.close()

    def _watch(self, collection: Collection) -> None:
        try:
            self.watcher.watch(collection.id, Path(collection.folder_path))
        except OSError as e:  # e.g. an unplugged drive: the indexer reports the missing folder
            self.watcher.unwatch(collection.id)
            log.warning("cannot watch %s: %s", collection.folder_path, e)
