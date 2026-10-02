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
        with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
            if response.status_code != 200:
                response.read()
                raise LLMError(f"HTTP {response.status_code}: {response.text[:500]}")
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    return
                choices = json.loads(data).get("choices") or []
                content = choices[0].get("delta", {}).get("content") if choices else None
                if content:
                    yield content

    def close(self) -> None:
        self._client.close()
