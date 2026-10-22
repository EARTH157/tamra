"""Shared LLM types: messages, streamed chunks, the provider protocol and its errors."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict


class Message(TypedDict):
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class Chunk:
    """A piece of streamed output: answer text, or the model's reasoning ("thinking")."""

    kind: Literal["text", "thinking"]
    text: str


# Thinking tokens count against max_tokens. Reserve room for them on top of the answer so a short
# max_tokens cannot be eaten before the answer starts (Claude 5 thinks adaptively even when not
# asked; local Qwen3 thinks when "Think longer" is on).
THINKING_HEADROOM = 2048

ErrorReason = Literal["offline", "auth", "quota", "model_missing", "model_unsupported", "other"]


class LLMError(Exception):
    pass


class ProviderError(LLMError):
    """A provider call failed. `reason` selects the user-facing message (spec §6 error table).

    The message never contains an API key.
    """

    def __init__(self, message: str, reason: ErrorReason = "other"):
        super().__init__(message)
        self.reason: ErrorReason = reason


class Provider(Protocol):
    """What the answer service needs from a model: local llama-server or a cloud API."""

    label: str
    kind: Literal["local", "api"]

    def generate(
        self, messages: list[Message], max_tokens: int = 1024, *, think: bool = False
    ) -> Iterator[Chunk]: ...

    def close(self) -> None: ...
