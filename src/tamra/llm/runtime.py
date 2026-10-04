"""The local LLM: owns the llama-server child process and hands out a client for it."""

import secrets
import threading
from collections.abc import Callable
from pathlib import Path

from tamra.llm.llama_server import LlamaServer, LlamaServerError
from tamra.llm.openai_compat import OpenAICompatibleLLM


class LocalLLM:
    def __init__(
        self,
        exe: Path,
        model: Path,
        log_file: Path,
        ctx_size: int = 8192,
        server_factory: Callable[..., LlamaServer] = LlamaServer,
    ):
        self._exe, self._model, self._log_file = exe, model, log_file
        self._ctx_size = ctx_size
        self._factory = server_factory
        self._lock = threading.Lock()
        self._server: LlamaServer | None = None
        self._client: OpenAICompatibleLLM | None = None

    @property
    def label(self) -> str:
        return self._model.stem

    @property
    def base_url(self) -> str | None:
        with self._lock:
            return self._server.base_url if self._server is not None else None

    def client(self) -> OpenAICompatibleLLM:
        """A client for the running server, starting (or restarting) llama-server if needed."""
        with self._lock:
            if self._server is not None and not self._server.alive():
                self._close_locked()
            if self._client is None:
                if not self._model.is_file():
                    raise LlamaServerError(f"Local model not found: {self._model}")
                key = secrets.token_urlsafe(32)
                server = self._factory(
                    self._exe, self._model, self._log_file, ctx_size=self._ctx_size, api_key=key
                )
                server.start()
                self._server = server
                self._client = OpenAICompatibleLLM(server.base_url, "local", api_key=key)
            return self._client

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._server is not None:
            self._server.stop()
            self._server = None
