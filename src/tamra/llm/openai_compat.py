import json
from collections.abc import Iterator
from typing import TypedDict

import httpx


class Message(TypedDict):
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMError(Exception):
    pass


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


class OpenAICompatibleLLM:
    """Streaming chat client for any OpenAI-compatible server (llama-server, Ollama, LM Studio)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self._model = model
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=httpx.Timeout(10.0, read=120.0),
            transport=transport,
        )

    def generate(self, messages: list[Message], max_tokens: int = 1024) -> Iterator[str]:
        body = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        try:
            with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
                if response.status_code != 200:
                    response.read()
                    raise LLMError(f"HTTP {response.status_code}: {response.text[:500]}")

                found_completion = False  # [DONE] or truthy finish_reason

                for line in _sse_lines(response.iter_bytes()):
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data:
                        continue

                    if data == "[DONE]":
                        found_completion = True
                        return

                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError as e:
                        raise LLMError(f"JSONDecodeError: {e}") from e

                    if not isinstance(payload, dict):
                        raise LLMError(f"Expected dict, got {type(payload).__name__}")

                    # Check for error key
                    if "error" in payload:
                        error = payload["error"]
                        if error is not None:  # null error is not an error
                            if isinstance(error, dict):
                                error_msg = error.get("message", str(error))
                            else:
                                error_msg = str(error)
                            raise LLMError(f"Stream error: {error_msg}")

                    # Extract and yield content
                    choices = payload.get("choices")
                    if isinstance(choices, list) and choices:
                        choice = choices[0]
                        if isinstance(choice, dict):
                            # Check for completion
                            finish_reason = choice.get("finish_reason")
                            if finish_reason:  # Truthy (non-empty string, not "")
                                found_completion = True

                            # Yield content if present
                            delta = choice.get("delta")
                            if isinstance(delta, dict):
                                content = delta.get("content")
                                if isinstance(content, str) and content:
                                    yield content

                # Stream ended: verify we saw completion
                if not found_completion:
                    raise LLMError("Stream ended without [DONE] or finish_reason")

        except LLMError:
            raise
        except (httpx.HTTPError, UnicodeDecodeError) as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e

    def close(self) -> None:
        self._client.close()
