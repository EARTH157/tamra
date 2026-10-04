"""The local LLM: owns the llama-server child process and hands out a client for it."""

import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from tamra.llm.llama_server import LlamaServer, LlamaServerError
from tamra.llm.openai_compat import OpenAICompatibleLLM


class LocalLLM:
    kind: Literal["local", "api"] = "local"

    def __init__(
        self,
        exe: Path,
        model_path: Callable[[], Path],
        log_file: Path,
        ctx_size: int = 8192,
        server_factory: Callable[..., LlamaServer] = LlamaServer,
    ):
        self._exe, self._model_path, self._log_file = exe, model_path, log_file
        self._ctx_size = ctx_size
        self._factory = server_factory
        self._lock = threading.Lock()
        self._server: LlamaServer | None = None
        self._client: OpenAICompatibleLLM | None = None
        self._running_model: Path | None = None

    @property
    def label(self) -> str:
        return self._model_path().stem

    # base_url and gpu_offload read one snapshot of `_server` without taking the lock: a UI poll
    # must not wait behind client(), which holds it for the whole llama-server start. `_server`
    # is only ever assigned (never mutated), once the server is up, so the read is atomic.
    @property
    def base_url(self) -> str | None:
        server = self._server
        return server.base_url if server is not None else None

    @property
    def gpu_offload(self) -> bool | None:
        """Whether the running server offloaded layers to the GPU: True, False (it fell back to
        the CPU), or None when there is no server or its log does not say."""
        server = self._server
        return server.gpu_offload if server is not None else None

    def client(self) -> OpenAICompatibleLLM:
        """A client for the running server, starting (or restarting) llama-server if needed.

        The model is read on every call: when it has changed, the server restarts on it.
        """
        with self._lock:
            model = self._model_path()
            if self._server is not None and (
                not self._server.alive() or self._running_model != model
            ):
                self._close_locked()
            if self._client is None:
                if not model.is_file():
                    raise LlamaServerError(f"Local model not found: {model}")
                key = secrets.token_urlsafe(32)
                server = self._factory(
                    self._exe, model, self._log_file, ctx_size=self._ctx_size, api_key=key
                )
                server.start()
                self._server = server
                self._running_model = model
                self._client = OpenAICompatibleLLM(
                    server.base_url, "local", api_key=key, kind="local", label=model.stem
                )
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
        self._running_model = None
