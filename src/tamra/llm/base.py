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


ErrorReason = Literal["offline", "auth", "quota", "model_missing", "other"]


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
