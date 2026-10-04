import json
from collections.abc import Iterator
from typing import Literal
from urllib.parse import urlparse, urlunparse

import httpx

from tamra.llm.base import Chunk, ErrorReason, LLMError, Message, ProviderError

__all__ = ["LLMError", "Message", "OpenAICompatibleLLM", "normalise_base_url"]

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = {"local": 300.0, "api": 120.0}  # seconds between bytes, not the whole answer
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _sse_lines(chunks: Iterator[bytes]) -> Iterator[str]:
    """Parse Server-Sent Events from byte chunks.

    Splits on LF only (handles CRLF), decodes UTF-8, yields each complete line.
    After the last chunk, yields any non-empty unterminated tail.
    """
    incomplete = b""
    for chunk in chunks:
        incomplete += chunk
        lines = incomplete.split(b"\n")
        incomplete = lines[-1]  # Keep incomplete last line
        for line_bytes in lines[:-1]:
            line_bytes = line_bytes.removesuffix(b"\r")
            yield line_bytes.decode("utf-8")
    # Yield final unterminated line if non-empty
    if incomplete:
        yield incomplete.removesuffix(b"\r").decode("utf-8")


def normalise_base_url(base_url: str) -> str:
    """Drop a trailing slash and a trailing /v1: requests add the /v1/... path themselves."""
    parts = urlparse(base_url.strip())
    path = parts.path.rstrip("/")
    path = path.removesuffix("/v1")
    return urlunparse(parts._replace(path=path)).rstrip("/")


def _http_reason(status: int) -> ErrorReason:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "quota"
    if status == 404:
        return "model_missing"
    return "other"


class _MaxTokensRejected(Exception):
    """The server does not know `max_tokens` (newer OpenAI models want max_completion_tokens)."""


class OpenAICompatibleLLM:
    """Streaming chat client for any OpenAI-compatible server (llama-server, Ollama, LM Studio)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        *,
        kind: Literal["local", "api"] = "api",
        label: str | None = None,
    ):
        self._model = model
        self.kind: Literal["local", "api"] = kind
        self.label = label or model
        base_url = normalise_base_url(base_url)
        # Loopback hosts bypass proxy settings; everything else honours them.
        trust_env = (urlparse(base_url).hostname or "") not in _LOOPBACK
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT[kind]),
            transport=transport,
            trust_env=trust_env,
        )

    def generate(
        self, messages: list[Message], max_tokens: int = 1024, *, think: bool = False
    ) -> Iterator[Chunk]:
        body: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if self.kind == "local":  # llama-server and Qwen3 honour this; strict clouds may reject it
            body["chat_template_kwargs"] = {"enable_thinking": think}
        try:
            yield from self._stream(body)
        except _MaxTokensRejected:
            body["max_completion_tokens"] = body.pop("max_tokens")
            yield from self._stream(body)

    def _stream(self, body: dict) -> Iterator[Chunk]:
        try:
            with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
                if response.status_code != 200:
                    response.read()
                    if (
                        response.status_code == 400
                        and "max_tokens" in body
                        and "max_tokens" in response.text
                    ):
                        raise _MaxTokensRejected
                    raise ProviderError(
                        f"HTTP {response.status_code}: {response.text[:500]}",
                        _http_reason(response.status_code),
                    )

                found_completion = False  # [DONE] or truthy finish_reason

                for line in _sse_lines(response.iter_bytes()):
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data:
                        continue

                    if data == "[DONE]":
                        return

                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError as e:
                        raise ProviderError(f"JSONDecodeError: {e}") from e

                    if not isinstance(payload, dict):
                        raise ProviderError(f"Expected dict, got {type(payload).__name__}")

                    # Check for error key
                    if "error" in payload:
                        error = payload["error"]
                        if error is not None:  # null error is not an error
                            if isinstance(error, dict):
                                error_msg = error.get("message", str(error))
                            else:
                                error_msg = str(error)
                            raise ProviderError(f"Stream error: {error_msg}")

                    choices = payload.get("choices")
                    if isinstance(choices, list) and choices:
                        choice = choices[0]
                        if isinstance(choice, dict):
                            # Check for completion
                            finish_reason = choice.get("finish_reason")
                            if finish_reason:  # Truthy (non-empty string, not "")
                                found_completion = True

                            delta = choice.get("delta")
                            if isinstance(delta, dict):
                                reasoning = delta.get("reasoning_content")
                                if isinstance(reasoning, str) and reasoning:
                                    yield Chunk("thinking", reasoning)
                                content = delta.get("content")
                                if isinstance(content, str) and content:
                                    yield Chunk("text", content)

                # Stream ended: verify we saw completion
                if not found_completion:
                    raise ProviderError("Stream ended without [DONE] or finish_reason")

        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise ProviderError(f"{type(e).__name__}: {e}", "offline") from e
        except (httpx.HTTPError, UnicodeDecodeError) as e:
            raise ProviderError(f"{type(e).__name__}: {e}") from e

    def close(self) -> None:
        self._client.close()
