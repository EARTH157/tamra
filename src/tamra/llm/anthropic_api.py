"""Anthropic provider: streams Claude answers through the official SDK."""

from collections.abc import Iterator
from typing import Any, Literal

import anthropic

from tamra.llm.base import Chunk, ErrorReason, Message, ProviderError

REQUEST_TIMEOUT = 120.0
# Claude 5 models think adaptively (a fixed `budget_tokens` is rejected with a 400), and thinking
# tokens count against max_tokens, so reserve room for them on top of the answer.
THINKING_HEADROOM = 2048


class AnthropicLLM:
    kind: Literal["local", "api"] = "api"

    def __init__(self, model: str, api_key: str, *, client: Any = None):
        self._model = model
        self.label = model
        # `client` lets tests inject a stub; the real one honours the system proxy and trust store.
        self._client = client or anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)

    def __repr__(self) -> str:  # never show the key held by the SDK client
        return f"AnthropicLLM(model={self._model!r})"

    def generate(
        self, messages: list[Message], max_tokens: int = 1024, *, think: bool = False
    ) -> Iterator[Chunk]:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": m["role"], "content": m["content"]}
                for m in messages
                if m["role"] != "system"
            ],
        }
        if system:
            params["system"] = system
        if think:
            params["thinking"] = {"type": "adaptive", "display": "summarized"}
            params["max_tokens"] = max_tokens + THINKING_HEADROOM
        try:
            with self._client.messages.stream(**params) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta" and delta.text:
                            yield Chunk("text", delta.text)
                        elif delta.type == "thinking_delta" and think and delta.thinking:
                            yield Chunk("thinking", delta.thinking)
                    elif event.type == "message_delta":
                        if getattr(event.delta, "stop_reason", None) == "refusal":
                            raise ProviderError("The model declined to answer this request.")
        except anthropic.AnthropicError as e:
            raise _provider_error(e) from e


def _provider_error(e: anthropic.AnthropicError) -> ProviderError:
    reason: ErrorReason = "other"
    if isinstance(e, anthropic.APIConnectionError):  # includes timeouts
        reason = "offline"
    elif isinstance(e, anthropic.CredentialsError):
        reason = "auth"
    elif isinstance(e, anthropic.APIStatusError):
        if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            reason = "auth"
        elif isinstance(e, anthropic.RateLimitError) or e.status_code == 402:
            reason = "quota"
        elif isinstance(e, anthropic.NotFoundError):
            reason = "model_missing"
        elif isinstance(e, anthropic.BadRequestError) and "credit balance" in e.message.lower():
            reason = "quota"
    detail = getattr(e, "message", None) or type(e).__name__
    return ProviderError(f"{type(e).__name__}: {detail}", reason)
