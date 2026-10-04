"""Core: owns the store, the indexer and watcher, the answer service, and the LLM providers."""

import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from tamra import secrets
from tamra.answer import AnswerService, AnswerSettings, Route
from tamra.embedder import MODEL_ID, Embedder
from tamra.ingest.chunker import TokenSpans, bge_token_spans
from tamra.ingest.indexer import EmbedderLike, Indexer
from tamra.ingest.watcher import FolderWatcher
from tamra.llm.anthropic_api import AnthropicLLM
from tamra.llm.base import Provider, ProviderError
from tamra.llm.openai_compat import OpenAICompatibleLLM
from tamra.llm.runtime import LocalLLM
from tamra.models.catalog import Catalog, load_catalog
from tamra.models.hardware import Hardware, detect
from tamra.models.manager import ModelManager
from tamra.settings import Settings, load_settings, save_settings
from tamra.store import Collection, Store

log = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com"
# Changing one of these swaps the provider. local_model_id does not: LocalLLM reads the model
# path on every question and restarts llama-server on the new model by itself.
_PROVIDER_FIELDS = frozenset({"mode", "api_provider", "api_model", "api_base_url"})


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
    ):
        """`llm` replaces the local runtime and `api_factory` builds the API provider from the
        settings and the key; both exist so tests can use fakes."""
        data_dir.mkdir(parents=True, exist_ok=True)
        bge = models_dir / "bge-m3"
        self.store = Store.open(data_dir / "tamra.db")
        self._embedder_factory = embedder_factory or (lambda: Embedder.load(bge))
        self._embedder: EmbedderLike | None = None
        self._embedder_lock = threading.Lock()
        spans_factory = token_spans_factory or (lambda: bge_token_spans(bge / "tokenizer.json"))
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
            llama_exe, self._local_model_path, data_dir / "logs" / "llama-server.log"
        )
        self.indexer = Indexer(self.store, self.embedder, spans_factory, MODEL_ID)
        self.watcher = FolderWatcher(self.indexer.request_reconcile, debounce=debounce)
        self.answers = AnswerService(
            self.store,
            self._embed_query,
            self._route,
            model_id=MODEL_ID,
            settings=answer_settings,
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

    def resolve_local_model(self) -> tuple[str, Path]:
        """The local model to serve, as (id, file): the selected one if installed, else the first
        installed catalog model, else the first GGUF that is not in the catalog. The id is a
        catalog id or "import:<file name>", so it is a valid `local_model_id`.

        Raises ProviderError (model_missing) when no model is installed.
        """
        installed = self._catalog.installed(self._models_dir)
        llm_ids = [m.id for m in self._catalog.llms() if m.id in installed]
        uncatalogued = self._catalog.uncatalogued(self._models_dir)
        wanted = self._settings.local_model_id
        if wanted in llm_ids:
            return wanted, installed[wanted]
        if wanted and wanted.startswith("import:"):
            name = wanted.removeprefix("import:").lower()
            for path in uncatalogued:
                if path.name.lower() == name:
                    return f"import:{path.name}", path
        if llm_ids:
            return llm_ids[0], installed[llm_ids[0]]
        if uncatalogued:
            return f"import:{uncatalogued[0].name}", uncatalogued[0]
        raise ProviderError("No local model is installed.", "model_missing")

    def _local_model_path(self) -> Path:
        return self.resolve_local_model()[1]

    # --- documents ---

    def embedder(self) -> EmbedderLike:
        """The embedding model, loaded on first use and shared by indexing and questions."""
        with self._embedder_lock:
            if self._embedder is None:
                self._embedder = self._embedder_factory()
            return self._embedder

    def _embed_query(self, text: str) -> np.ndarray:
        return self.embedder().embed([text])[0]

    def start(self) -> None:
        self.indexer.start()
        collection = self.store.get_collection()
        if collection is not None:
            self._watch(collection)
            self.indexer.request_reconcile()

    def set_collection(self, name: str, folder: str) -> Collection:
        """Index this folder from now on (M1 keeps one collection; chats are kept)."""
        path = Path(folder.strip()).expanduser()
        if not path.is_absolute():
            raise ValueError("Choose a full folder path, for example C:\\Users\\me\\Documents.")
        if not path.is_dir():
            raise ValueError(f"Folder not found: {folder}")
        collection = self.store.replace_collection(name.strip() or path.name, str(path), MODEL_ID)
        self._watch(collection)
        self.indexer.request_reconcile()
        return collection

    def rebuild(self) -> bool:
        if self.store.get_collection() is None:
            return False
        self.indexer.request_rebuild()
        return True

    def index_status(self) -> dict | None:
        collection = self.store.get_collection()
        if collection is None:
            return None
        state = self.indexer.state()
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
            self.watcher.watch(Path(collection.folder_path))
        except OSError as e:  # e.g. an unplugged drive: the indexer reports the missing folder
            self.watcher.stop()
            log.warning("cannot watch %s: %s", collection.folder_path, e)
