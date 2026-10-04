"""Core: owns the store, the indexer and folder watcher, the answer service, and the local LLM."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from tamra.answer import AnswerService, AnswerSettings
from tamra.embedder import MODEL_ID, Embedder
from tamra.ingest.chunker import TokenSpans, bge_token_spans
from tamra.ingest.indexer import EmbedderLike, Indexer
from tamra.ingest.watcher import FolderWatcher
from tamra.llm.runtime import LocalLLM
from tamra.store import Collection, Store

log = logging.getLogger(__name__)

DEV_LLM_FILE = "qwen2.5-0.5b-instruct-q4_k_m.gguf"  # until M2 brings the model catalog


class Core:
    def __init__(
        self,
        data_dir: Path,
        models_dir: Path,
        llama_exe: Path,
        *,
        embedder_factory: Callable[[], EmbedderLike] | None = None,
        token_spans_factory: Callable[[], TokenSpans] | None = None,
        llm=None,
        answer_settings: AnswerSettings | None = None,
        debounce: float = 2.0,
    ):
        data_dir.mkdir(parents=True, exist_ok=True)
        bge = models_dir / "bge-m3"
        self.store = Store.open(data_dir / "tamra.db")
        self._embedder_factory = embedder_factory or (lambda: Embedder.load(bge))
        self._embedder: EmbedderLike | None = None
        self._embedder_lock = threading.Lock()
        spans_factory = token_spans_factory or (lambda: bge_token_spans(bge / "tokenizer.json"))
        self.llm = llm or LocalLLM(
            llama_exe, models_dir / DEV_LLM_FILE, data_dir / "logs" / "llama-server.log"
        )
        self.indexer = Indexer(self.store, self.embedder, spans_factory, MODEL_ID)
        self.watcher = FolderWatcher(self.indexer.request_reconcile, debounce=debounce)
        self.answers = AnswerService(
            self.store,
            self._embed_query,
            self.llm.client,
            model_id=MODEL_ID,
            llm_label=lambda: self.llm.label,
            settings=answer_settings,
        )

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
        self.llm.close()
        self.store.close()

    def _watch(self, collection: Collection) -> None:
        try:
            self.watcher.watch(Path(collection.folder_path))
        except OSError as e:  # e.g. an unplugged drive: the indexer reports the missing folder
            self.watcher.stop()
            log.warning("cannot watch %s: %s", collection.folder_path, e)
