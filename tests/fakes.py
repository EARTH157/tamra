"""Test doubles shared by several test modules."""

import re
import zlib
from collections.abc import Iterator

import numpy as np
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from tamra.llm.base import Chunk
from tamra.ocr import OcrPageResult


class FakeOcr:
    """Reads every page as the same text with one box per code point. `fail_calls` are the call
    numbers (1-based) that raise; `on_read(call)` runs at the start of each call."""

    BOX = (0.1, 0.2, 0.3, 0.25)

    def __init__(self, text="Scanned page about the boiler warranty.", fail_calls=(), on_read=None):
        self.text = text
        self.fail_calls = set(fail_calls)
        self.on_read = on_read
        self.calls = 0

    def read(self, image: np.ndarray) -> OcrPageResult:
        self.calls += 1
        if self.on_read is not None:
            self.on_read(self.calls)
        if self.calls in self.fail_calls:
            raise RuntimeError("OCR broke")
        boxes = np.tile(np.array(self.BOX, np.float32), (len(self.text), 1))
        return OcrPageResult(self.text, boxes)


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
    gpu_offload: bool | None = None

    def __init__(self, llm: "FakeLLM | None" = None):
        self.llm = llm or FakeLLM()
        self.closed = False

    def client(self) -> "FakeLLM":
        return self.llm

    def close(self) -> None:
        self.closed = True


class MemoryKeyring(KeyringBackend):
    """In-memory backend: tests never touch the real Windows Credential Manager."""

    priority = 1

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        try:
            del self.store[(service, username)]
        except KeyError:
            raise PasswordDeleteError("not found") from None
