import json
from collections.abc import Iterator
from typing import TypedDict

import httpx


class Message(TypedDict):
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMError(Exception):
    pass


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

                stream_ended = False
                incomplete_line = b""

                # Iterate over raw bytes to properly handle UTF-8 and SSE line splitting
                for chunk in response.iter_bytes():
                    incomplete_line += chunk

                    # Split on LF only (handle CRLF by stripping CR afterward)
                    lines = incomplete_line.split(b"\n")
                    incomplete_line = lines[-1]  # Keep incomplete last line

                    # Process all complete lines
                    for line_bytes in lines[:-1]:
                        # Strip trailing CR if present (converts CRLF -> LF)
                        line_bytes = line_bytes.rstrip(b"\r")

                        try:
                            line = line_bytes.decode("utf-8")
                        except UnicodeDecodeError as e:
                            raise LLMError("Invalid UTF-8 in stream") from e

                        if not line.startswith("data:"):
                            continue

                        data = line[len("data:") :].strip()

                        # Skip empty data: payloads
                        if not data:
                            continue

                        if data == "[DONE]":
                            stream_ended = True
                            return

                        try:
                            payload = json.loads(data)
                        except json.JSONDecodeError as e:
                            raise LLMError("invalid JSON in stream") from e

                        # Check for error key in payload
                        if "error" in payload:
                            error_msg = payload["error"].get("message", str(payload["error"]))
                            raise LLMError(f"Stream error: {error_msg}")

                        # Check for finish_reason to mark stream complete
                        choices = payload.get("choices") or []
                        if choices and choices[0].get("finish_reason") is not None:
                            stream_ended = True
                            # Yield any content from this final chunk
                            delta = choices[0].get("delta")
                            if delta and "content" in delta:
                                content = delta.get("content")
                                if content:
                                    yield content
                            return

                        # Normal case: yield content from delta
                        if choices:
                            delta = choices[0].get("delta")
                            if delta and "content" in delta:
                                content = delta.get("content")
                                if content:
                                    yield content

                # Process any remaining incomplete line at end of stream
                if incomplete_line and incomplete_line.strip():
                    line_bytes = incomplete_line.rstrip(b"\r")
                    try:
                        line = line_bytes.decode("utf-8")
                    except UnicodeDecodeError as e:
                        raise LLMError("Invalid UTF-8 in stream") from e

                    if line.startswith("data:"):
                        data = line[len("data:") :].strip()
                        if data and data != "[DONE]":
                            try:
                                payload = json.loads(data)
                                if "error" in payload:
                                    error_msg = payload["error"].get(
                                        "message", str(payload["error"])
                                    )
                                    raise LLMError(f"Stream error: {error_msg}")
                            except json.JSONDecodeError as e:
                                raise LLMError("invalid JSON in stream") from e

                # If stream ended without [DONE] or finish_reason, raise error
                if not stream_ended:
                    raise LLMError("stream ended without [DONE] or finish_reason")

        except LLMError:
            raise
        except httpx.HTTPError as e:
            raise LLMError(f"HTTP error: {e}") from e

    def close(self) -> None:
        self._client.close()
