"""Test doubles shared by several test modules."""

import re
import zlib
from collections.abc import Iterator

import numpy as np


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
    """Yields fixed tokens and records the messages it was given."""

    def __init__(self, tokens: tuple[str, ...] = ("The lease is three years ", "[1]", ".")):
        self.tokens = tokens
        self.calls: list[list[dict]] = []

    def generate(self, messages, max_tokens: int = 1024) -> Iterator[str]:
        self.calls.append(list(messages))
        yield from self.tokens
