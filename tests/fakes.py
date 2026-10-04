"""Test doubles shared by several test modules."""

import re
import zlib
from collections.abc import Iterator

import numpy as np

from tamra.llm.base import Chunk


class FakeEmbedder:
    """Bag-of-words hashing embedder: texts that share words get similar unit vectors."""

    dim = 1024

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        self.calls += 1
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in set(re.findall(r"\w+", text.lower())):
                out[row, zlib.crc32(word.encode("utf-8")) % self.dim] += 1.0
            norm = np.linalg.norm(out[row])
            if norm:
                out[row] /= norm
            else:
                out[row, 0] = 1.0
        return out


def fake_spans(text: str) -> list[tuple[int, int]]:
    """One token per whitespace-separated word."""
    return [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]


class FakeLLM:
    """Yields fixed chunks (plain strings are answer text) and records what it was given."""

    def __init__(
        self,
        tokens: tuple[str | Chunk, ...] = ("The lease is three years ", "[1]", "."),
        *,
        kind: str = "local",
        label: str = "fake-llm",
    ):
        self.tokens = tokens
        self.kind = kind
        self.label = label
        self.calls: list[list[dict]] = []
        self.think_flags: list[bool] = []
        self.closed = False

    def generate(self, messages, max_tokens: int = 1024, *, think: bool = False) -> Iterator[Chunk]:
        self.calls.append(list(messages))
        self.think_flags.append(think)
        for token in self.tokens:
            yield token if isinstance(token, Chunk) else Chunk("text", token)

    def close(self) -> None:
        self.closed = True


class FakeLocalLLM:
    """Stands in for tamra.llm.runtime.LocalLLM."""

    label = "fake-model"

    def __init__(self, llm: "FakeLLM | None" = None):
        self.llm = llm or FakeLLM()
        self.closed = False

    def client(self) -> "FakeLLM":
        return self.llm

    def close(self) -> None:
        self.closed = True
