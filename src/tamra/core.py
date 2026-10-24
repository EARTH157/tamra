"""Core: owns the store, the indexer and watcher, the answer service, and the LLM providers."""

import hashlib
import logging
import os
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from tamra import secrets
from tamra.answer import (
    API_CONTEXT_TOKENS,
    AnswerService,
    AnswerSettings,
    Route,
    is_notes,
    source_payloads,
)
from tamra.attribution import Attributor, Match
from tamra.drives import is_removable
from tamra.embedder import MODEL_ID, Embedder, EmbedderUnavailable
from tamra.ingest.chunker import TokenSpans, bge_token_spans
from tamra.ingest.indexer import EmbedderLike, Indexer, OcrLike
from tamra.ingest.parsers import SUPPORTED, ParsedDoc, ParseError, parse_file, with_ocr
from tamra.ingest.watcher import FolderWatcher
from tamra.llm.anthropic_api import AnthropicLLM
from tamra.llm.base import Provider, ProviderError
from tamra.llm.openai_compat import OpenAICompatibleLLM
from tamra.llm.runtime import LocalLLM
from tamra.models.catalog import Catalog, load_catalog
from tamra.models.external import ExternalModel
from tamra.models.external import scan as scan_external
from tamra.models.gguf import read_shape
from tamra.models.hardware import Hardware, context_size, detect
from tamra.models.manager import ImportResult, ModelManager
from tamra.ocr import MODEL_DIR as OCR_MODEL_DIR
from tamra.ocr import OcrEngine
from tamra.reranker import MODEL_DIR as RERANKER_MODEL_DIR
from tamra.reranker import Reranker
from tamra.settings import LinkedModel, Settings, load_settings, save_settings
from tamra.store import Collection, FileRecord, MessageRecord, SourceRecord, Store
from tamra.viewer import FileMissing, Unreadable, ViewerError, resolve_file

log = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com"
# Changing one of these swaps the provider. local_model_id does not: LocalLLM reads the model
# path on every question and restarts llama-server on the new model by itself.
_PROVIDER_FIELDS = frozenset({"mode", "api_provider", "api_model", "api_base_url"})
DOCUMENT_CACHE_SIZE = 8  # parsed documents kept for the viewer, so paging a PDF parses it once
DOCUMENT_CACHE_MAX_BYTES = 16 * 1024 * 1024  # a larger file is parsed per request, not kept
# shortcut: a model added in another tool shows up within this many seconds; a filesystem watch
# on the stores would make it instant if that ever matters.
EXTERNAL_TTL = 10.0


class AnswerBusy(RuntimeError):
    """An answer is being written, so a project cannot be deleted right now."""


class BadName(ValueError):
    """A project name that is empty, too long, or has control or format characters."""


class BadNotes(ValueError):
    """Project notes over MAX_NOTES characters, or with a control character or a bidi override."""


class FolderUsed(ValueError):
    """The folder is, or lies inside or around, another project's folder."""


class FolderMissing(ValueError):
    """The path is not an absolute, existing directory."""


class FolderNotAllowed(ValueError):
    """A whole drive or share, or a folder that holds or lies inside Tamra's own folders."""


class NoProjects(LookupError):
    """There is no project to search."""


class UnknownProject(LookupError):
    """A project name that matches no project."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def _nocase(name: str) -> str:
    """The name as SQLite's NOCASE compares it, folding only A-Z: the store keeps project names
    unique that way, so at most one project matches."""
    return "".join(c.lower() if c.isascii() else c for c in name)


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


MAX_NOTES = 1000
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")


def _clean_notes(notes: str) -> str:
    """Notes as they are stored (project notes spec): one kind of new line, stripped, at most
    MAX_NOTES characters, and no control characters (new lines and tabs aside) or bidi
    overrides. Other format characters stay: a zero-width space comes with pasted Thai text."""
    cleaned = notes.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(cleaned) > MAX_NOTES:
        raise BadNotes("notes too long")
    if any(
        c in _BIDI_CONTROLS or (unicodedata.category(c) == "Cc" and c not in "\n\t")
        for c in cleaned
    ):
        raise BadNotes("bad notes characters")
    return cleaned


class FileNameNotAllowed(ValueError):
    """An added file's name that is not a plain Windows file name of a supported type."""


_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def _file_name(name: str) -> str:
    """name if it is a plain file name Windows accepts, with a type Tamra indexes and not one the
    indexer skips; FileNameNotAllowed otherwise. A path, drive or `..` never passes."""
    if (
        not name
        or name != name.strip(" .")  # Windows drops trailing dots and spaces
        or name.startswith((".", "~$"))
        or any(c in '<>:"/\\|?*' or unicodedata.category(c) in _HIDDEN for c in name)
        or name.split(".")[0].upper() in _RESERVED
        or Path(name).suffix.lower() not in SUPPORTED
    ):
        raise FileNameNotAllowed(name)
    return name


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


@dataclass(frozen=True)
class LocalChoice:
    """A local model Tamra can run: its `local_model_id`, file, label, and where the file is."""

    id: str
    path: Path
    label: str
    source: str  # "tamra" | "linked" | "ollama" | "lmstudio" | "huggingface"
    catalogued: bool


def _path_key(path: Path | str) -> str:
    return os.path.normcase(str(path))


def _same_id(a: str, b: str) -> bool:
    """Ids naming files compare as Windows paths do (case-insensitively); catalog ids exactly."""
    if a.startswith(("import:", "file:")):
        return _path_key(a) == _path_key(b)
    return a == b


def _intact(link: LinkedModel) -> bool:
    """A linked file is used while it is still there with the size it had when it was linked."""
    try:
        path = Path(link["path"])
        return path.is_file() and path.stat().st_size == link["size"]
    except OSError:
        return False


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
        ocr_factory: Callable[[], OcrLike | None] | None = None,
        reranker_factory: Callable[[], Reranker | None] | None = None,
        api_factory: Callable[[Settings, str], Provider] = _build_api_provider,
        catalog: Catalog | None = None,
        external_scan: Callable[[], list[ExternalModel]] | None = None,
        answer_settings: AnswerSettings | None = None,
        debounce: float = 2.0,
        warm_attribution: bool = False,
    ):
        """`llm` replaces the local runtime, `ocr_factory` the OCR models, `reranker_factory`
        the reranker, and `api_factory`
        builds the API provider from the settings and the key; they exist so tests can use
        fakes, as does `external_scan` for the scan of other tools' model stores.
        `warm_attribution` embeds a new answer's sources in the background so the first
        "Check source" is fast."""
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
        self.models = ModelManager(models_dir, self._catalog, on_installed=self._model_installed)
        # Looked up at call time, so tests can patch tamra.core.scan_external.
        self._external_scan = external_scan or (lambda: scan_external(Path.home(), os.environ))
        self._external: tuple[float, list[ExternalModel]] | None = None
        self._external_lock = threading.Lock()
        self._links_lock = threading.Lock()  # one read-modify-write of linked_models at a time
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
            ctx_size=self.local_context,
            label_for=self._local_label,
        )
        self._ocr_factory = ocr_factory or self._load_ocr
        self._ocr: OcrLike | None = None
        self._ocr_failed = False  # the load raised: not tried again until the models are installed
        self._ocr_lock = threading.Lock()
        self._reranker_factory = reranker_factory or self._load_reranker
        self._reranker: Reranker | None = None
        self._reranker_failed = False  # as _ocr_failed
        self._reranker_lock = threading.Lock()
        self.indexer = Indexer(
            self.store, self.embedder, spans_factory, MODEL_ID, ocr=self.ocr_engine
        )
        self.watcher = FolderWatcher(self.indexer.request_reconcile, debounce=debounce)
        self.answers = AnswerService(
            self.store,
            self._embed_query,
            self._route,
            model_id=MODEL_ID,
            settings=answer_settings,
            on_saved=self._warm if warm_attribution else None,
            rerank=self._rerank,
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
            return Route("local", self.local.client, self.local.ctx_size)
        return Route(settings.api_provider, lambda: self._open_api(settings), API_CONTEXT_TOKENS)

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

    def external_models(self) -> list[ExternalModel]:
        """The models Ollama, LM Studio and the Hugging Face cache hold, scanned at most every
        EXTERNAL_TTL seconds (GET /api/models is polled every second). A failing scan lists none."""
        with self._external_lock:
            now = time.monotonic()
            if self._external is None or now - self._external[0] >= EXTERNAL_TTL:
                try:
                    found = self._external_scan()
                except Exception:  # a broken store must never break the model list
                    log.exception("scanning other tools' model folders failed")
                    found = []
                self._external = (now, found)
            return self._external[1]

    def _forget_scan(self) -> None:
        with self._external_lock:
            self._external = None

    def local_choices(self) -> list[LocalChoice]:
        """Every local model Tamra can run, in the order resolve_local_model falls back through:
        catalog LLMs (from Tamra's folder, else a linked file, else another tool, matched by
        sha256), then uncatalogued files in Tamra's folder, then linked files, then other tools'.

        Only sizes are checked here; nothing is hashed (linked files carry their sha256 from the
        import, and Ollama and Hugging Face name their files by it)."""
        llms = self._catalog.llms()
        installed = self._catalog.installed(self._models_dir)
        links = [link for link in self._settings.linked_models if _intact(link)]
        scanned = self.external_models()
        catalog_hashes = {e.main_file.sha256.lower() for e in llms}
        hashed: dict[str, tuple[Path, str]] = {}
        for link in links:
            hashed.setdefault(link["sha256"], (Path(link["path"]), "linked"))
        for m in scanned:
            if m.sha256 is not None:
                hashed.setdefault(m.sha256.lower(), (m.path, m.source))

        choices: list[LocalChoice] = []
        for entry in llms:
            sha = entry.main_file.sha256.lower()
            if entry.id in installed:
                path, source = installed[entry.id], "tamra"
            elif sha in hashed:
                path, source = hashed[sha]
            else:
                continue
            choices.append(LocalChoice(entry.id, path, entry.name, source, True))
        for path in self._catalog.uncatalogued(self._models_dir):
            choices.append(LocalChoice(f"import:{path.name}", path, path.stem, "tamra", False))
        offered: set[str] = set()
        for link in links:
            path = Path(link["path"])
            if link["sha256"] not in catalog_hashes and _path_key(path) not in offered:
                offered.add(_path_key(path))
                choices.append(LocalChoice(f"file:{path}", path, path.stem, "linked", False))
        for m in scanned:
            if (m.sha256 or "").lower() not in catalog_hashes and _path_key(m.path) not in offered:
                offered.add(_path_key(m.path))
                choices.append(LocalChoice(f"file:{m.path}", m.path, m.name, m.source, False))
        return choices

    def import_model(self, path: Path) -> ImportResult:
        """Import a model file. An LLM file is used where it is (linked) and selected, unless it
        is on a removable drive, which may be gone next time: it is then copied in. Embedding and
        OCR files are always copied. Raises ImportRefused, and OSError when a copy fails."""
        result = self.models.import_file(path, link_llm=not is_removable(path))
        if result.linked:
            link: LinkedModel = {
                "path": str(result.path),
                "size": result.size,
                "sha256": result.sha256,
            }
            with self._links_lock:
                kept = [
                    old
                    for old in self._settings.linked_models
                    if _path_key(old["path"]) != _path_key(link["path"])
                ]
                self.apply_settings({"linked_models": [*kept, link], "local_model_id": result.id})
        self._forget_scan()
        return result

    def unlink_model(self, path: str) -> None:
        """Stop using a linked file; the file itself is left alone. KeyError if it is not linked."""
        with self._links_lock:
            links = self._settings.linked_models
            kept = [link for link in links if _path_key(link["path"]) != _path_key(path)]
            if len(kept) == len(links):
                raise KeyError(path)
            self.apply_settings({"linked_models": kept})
        self._forget_scan()

    def resolve_local_model(self) -> tuple[str, Path, str]:
        """The local model to serve, as (id, file, label): the selected one if it is available,
        else the first of local_choices(). The id is a valid `local_model_id`.

        Raises ProviderError (model_missing) when no model is available.
        """
        choices = self.local_choices()
        wanted = self._settings.local_model_id
        chosen = next((c for c in choices if wanted and _same_id(c.id, wanted)), None)
        if chosen is None and choices:
            chosen = choices[0]
        if chosen is None:
            raise ProviderError("No local model is installed.", "model_missing")
        return chosen.id, chosen.path, chosen.label

    def local_context(self, model: Path, choice: str | None = None) -> int:
        """The context window for the model file: the setting (or `choice`), where "auto" fits
        it to this computer's memory and the model's own limit (spec §9)."""
        try:
            size = model.stat().st_size
        except OSError:
            size = 0
        return context_size(
            choice or self._settings.local_context, read_shape(model), size, self.hardware()
        )

    def _local_model_path(self) -> Path:
        return self.resolve_local_model()[1]

    def _local_label(self, path: Path) -> str:
        """The name to show for the model file `path`: its catalog or Ollama name, else its stem."""
        for choice in self.local_choices():
            if choice.path == path:
                return choice.label
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

    def ocr_engine(self) -> OcrLike | None:
        """The OCR models, loaded on first use and kept. None while they are not installed or
        cannot be loaded; scanned pages then wait. Models that failed to load are not tried
        again until they are installed again or the app restarts."""
        with self._ocr_lock:
            if self._ocr is None and not self._ocr_failed:
                try:
                    self._ocr = self._ocr_factory()
                except Exception as e:  # damaged or unreadable model files
                    log.warning("the OCR models cannot be loaded: %s", e)
                    self._ocr_failed = True
            return self._ocr

    def _ocr_installed(self) -> bool:
        entry = self._catalog.ocr()
        return entry is not None and entry.id in self._catalog.installed(self._models_dir)

    def _load_ocr(self) -> OcrLike | None:
        if not self._ocr_installed():
            return None
        return OcrEngine.load(self._models_dir / OCR_MODEL_DIR)

    def reranker(self) -> Reranker | None:
        """The reranker, loaded on first use and kept. None while it is not installed or cannot
        be loaded; answers then keep the fused order. A reranker that failed to load is not tried
        again until it is installed again or the app restarts."""
        with self._reranker_lock:
            if self._reranker is None and not self._reranker_failed:
                try:
                    self._reranker = self._reranker_factory()
                except Exception as e:  # damaged or unreadable model files
                    log.warning("the reranker cannot be loaded: %s", e)
                    self._reranker_failed = True
                else:
                    if self._reranker is not None:
                        log.info("the reranker is loaded")
            return self._reranker

    def _load_reranker(self) -> Reranker | None:
        entry = self._catalog.reranker()
        if entry is None or entry.id not in self._catalog.installed(self._models_dir):
            return None
        return Reranker.load(self._models_dir / RERANKER_MODEL_DIR)

    def _rerank(self, query: str, texts: list[str]) -> np.ndarray | None:
        reranker = self.reranker()
        return None if reranker is None else reranker.score(query, texts)

    def _model_installed(self, model_id: str) -> None:
        """The search model arrived: files that paused for lack of it are indexed now. The OCR
        models arrived: every file with pages left for OCR is read again, in every project. The
        reranker arrived: the next question loads it."""
        ocr = self._catalog.ocr()
        reranker = self._catalog.reranker()
        if ocr is not None and model_id == ocr.id:
            with self._ocr_lock:
                self._ocr_failed = False  # new files: load them on the next scanned page
            self.store.requeue_ocr_files()
            self.indexer.request_reconcile()
        elif model_id == self._catalog.embedding().id:
            self.indexer.request_reconcile()
        elif reranker is not None and model_id == reranker.id:
            with self._reranker_lock:
                self._reranker_failed = False

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
        if is_notes(source):
            return False  # the saved notes are what the answer saw; there is no file to change
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
        """The file parsed as it is now, with the OCR text the index used for its scanned pages,
        from a small cache keyed by size, modified time and index time."""
        record, path = self.file_path(file_id)
        try:
            stat = path.stat()
        except OSError as e:
            raise FileMissing("file not found") from e
        key = (file_id, str(path), stat.st_size, stat.st_mtime_ns, record.indexed_at)
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
        scanned = self.store.ocr_pages(file_id)
        if scanned:  # the index used OCR text for these pages, so the viewer must too
            doc = with_ocr(doc, {p.page: p.text for p in scanned})
        if stat.st_size > DOCUMENT_CACHE_MAX_BYTES:
            return record, path, doc
        with self._documents_lock:
            self._documents[key] = doc
            while len(self._documents) > DOCUMENT_CACHE_SIZE:
                self._documents.popitem(last=False)
        return record, path, doc

    # --- projects ---

    def start(self) -> None:
        # A file read while the models arrived, or a crash before the re-queue, would otherwise
        # wait for a change. The flag starts unset on each launch, so a restart retries once.
        if self._ocr_installed() and not self._ocr_failed:
            self.store.requeue_ocr_files()
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
            "notes": collection.notes,
            **self._index_status(collection),
        }

    def create_project(self, name: str, folder: str) -> Collection:
        """Add a project and index its folder. ValueError (with a message safe to show) if the
        folder is not usable, is already used by another project, or the name is taken.

        Chats that have no home project are general chats (spec §6): a new project leaves them
        where they are."""
        with self._projects_lock:
            path = self._checked_folder(folder)
            collection = self.store.create_collection(
                _clean_name(name) if name.strip() else _derived_name(path), str(path), MODEL_ID
            )
            self._watch(collection)  # under the lock: a delete cannot unwatch before this
        self.indexer.request_reconcile(collection.id)
        return collection

    def add_file(self, collection_id: int, name: str, content: bytes) -> Path | None:
        """Save a new file in the project's folder and index it; None if the project is unknown.

        FileNameNotAllowed for a bad name, FileExistsError if the folder has the name already:
        an existing file is never overwritten (spec §5)."""
        collection = self.store.get_collection(collection_id)
        if collection is None:
            return None
        target = Path(collection.folder_path) / _file_name(name)
        if target.exists():
            raise FileExistsError(name)
        # Written under a name the watcher and indexer skip, then renamed in one step, so a
        # half-written file is never indexed.
        temp = target.with_name(f"~$tamra-{uuid.uuid4().hex[:8]}{target.suffix}")
        try:
            temp.write_bytes(content)
            os.rename(temp, target)  # on Windows it refuses an existing target
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        self.indexer.request_reconcile(collection.id)  # also where the folder cannot be watched
        return target

    def change_project(
        self, collection_id: int, *, name: str | None = None, notes: str | None = None
    ) -> Collection | None:
        """Rename a project and/or set its notes; None if it is unknown. Both are checked before
        either is saved (BadName, BadNotes, NameTaken), so a refused change changes nothing."""
        new_name = _clean_name(name) if name is not None else None
        new_notes = _clean_notes(notes) if notes is not None else None
        if self.store.get_collection(collection_id) is None:
            return None
        # rename_collection raises NameTaken before it writes, and runs before the notes are saved.
        if new_name is not None and not self.store.rename_collection(collection_id, new_name):
            return None
        if new_notes is not None and not self.store.set_collection_notes(collection_id, new_notes):
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

    def search(self, query: str, names: list[str] | None, k: int) -> tuple[list[dict], list[str]]:
        """The best k passages for an AI app (MCP spec §5), as `source_payloads` builds them,
        and the names of the stale projects left out. `names` limits the search to those
        projects (matched ignoring case); None searches every project. Nothing is saved.

        Raises NoProjects, UnknownProject, StaleIndex, NothingIndexed or EmbedderUnavailable.
        """
        collections = self.store.list_collections()
        if not collections:
            raise NoProjects("no projects")
        if names is not None:
            by_name = {_nocase(c.name): c for c in collections}
            chosen: list[Collection] = []
            for name in names:
                collection = by_name.get(_nocase(name))
                if collection is None:
                    raise UnknownProject(name)
                if collection not in chosen:
                    chosen.append(collection)
            collections = chosen
        searched, skipped = self.answers.searchable(collections)
        sources = self.answers.passages(
            [c.id for c in searched], self._embed_query(query), query, k
        )
        # Counts only: the query and the passages never go to the log.
        log.info("mcp search: %d project(s), k=%d, %d passage(s)", len(searched), k, len(sources))
        return source_payloads(self.store, sources), skipped

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
            "reading": None
            if state.reading is None
            else {"page": state.reading[0], "pages": state.reading[1]},
            "problems": [
                {
                    "rel_path": f.rel_path,
                    "status": f.status,
                    "error": f.error,
                    "needs_ocr": f.needs_ocr,
                }
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
