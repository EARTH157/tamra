"""What a GGUF model file says about its context: the length it was trained for and the size of
its KV cache per token (spec §9). Reads only the metadata at the start of the file."""

import functools
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

Q8_0_BYTES = 34 / 32  # a q8_0 block: 32 one-byte values and a two-byte scale
SERVER_SLOTS = 4  # llama-server's parallel slots: each keeps its own sliding window

# GGUF value types (llama.cpp gguf.h): fixed sizes, then string (8), array (9).
_FIXED = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_INTS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 7: "<?", 10: "<Q", 11: "<q"}
_STRING, _ARRAY = 8, 9


@dataclass(frozen=True)
class ModelShape:
    context_length: int | None  # what the model was trained for; None when the file does not say
    kv_bytes_per_token: int  # the KV cache of one token in full-attention layers, at q8_0
    kv_fixed_bytes: int = 0  # sliding-window layers: a cache of the window, whatever the context


class GGUFError(Exception):
    """Not a GGUF file, or one whose metadata cannot be read."""


def _read(f: BinaryIO, n: int) -> bytes:
    # A damaged length must not make read() allocate more than the file holds.
    if n > os.fstat(f.fileno()).st_size - f.tell():
        raise GGUFError("the file ends inside its metadata")
    data = f.read(n)
    if len(data) != n:
        raise GGUFError("the file ends inside its metadata")
    return data


def _u32(f: BinaryIO) -> int:
    return struct.unpack("<I", _read(f, 4))[0]


def _u64(f: BinaryIO) -> int:
    return struct.unpack("<Q", _read(f, 8))[0]


def _string(f: BinaryIO) -> str:
    return _read(f, _u64(f)).decode("utf-8", errors="replace")


def _value(f: BinaryIO, kind: int, wanted: bool) -> object:
    """One value: ints (and arrays of ints) when wanted, strings always; the rest is skipped."""
    if kind in _INTS:
        raw = _read(f, _FIXED[kind])
        return struct.unpack(_INTS[kind], raw)[0] if wanted else None
    if kind in _FIXED:
        _read(f, _FIXED[kind])
        return None
    if kind == _STRING:
        return _string(f)
    if kind == _ARRAY:
        item, count = _u32(f), _u64(f)
        if wanted and item in _INTS:
            return [_value(f, item, True) for _ in range(count)]
        if item in _FIXED:
            f.seek(_FIXED[item] * count, 1)
        else:  # strings or nested arrays (a tokenizer's vocabulary): skipped one by one
            for _ in range(count):
                _value(f, item, False)
        return None
    raise GGUFError(f"unknown metadata type {kind}")


def read_metadata(path: Path, keys: set[str] | None = None) -> dict[str, object]:
    """The GGUF metadata: every string value, and the integer values whose key is in `keys`
    (an architecture's keys may be named with `{arch}`, filled in from general.architecture)."""
    found: dict[str, object] = {}
    with path.open("rb") as f:
        if _read(f, 4) != b"GGUF":
            raise GGUFError("not a GGUF file")
        version = _u32(f)
        if version < 2:
            raise GGUFError(f"GGUF version {version} is too old")
        _u64(f)  # tensor count
        for _ in range(_u64(f)):
            key = _string(f)
            arch = found.get("general.architecture", "")
            wanted = keys is not None and key in {k.replace("{arch}", str(arch)) for k in keys}
            value = _value(f, _u32(f), wanted)
            if value is not None:
                found[key] = value
    return found


_SHAPE_KEYS = {
    "{arch}.context_length",
    "{arch}.block_count",
    "{arch}.embedding_length",
    "{arch}.attention.head_count",
    "{arch}.attention.head_count_kv",
    "{arch}.attention.key_length",
    "{arch}.attention.value_length",
    "{arch}.attention.key_length_swa",
    "{arch}.attention.value_length_swa",
    "{arch}.attention.sliding_window",
    "{arch}.attention.sliding_window_pattern",
    "{arch}.attention.shared_kv_layers",
}


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def read_shape(path: Path) -> ModelShape | None:
    """The model's context length and KV size per token; None when the file does not tell."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return _cached_shape(path, stat.st_size, stat.st_mtime_ns)


@functools.lru_cache(maxsize=16)
def _cached_shape(path: Path, size: int, mtime_ns: int) -> ModelShape | None:
    """read_shape for one version of the file (its size and time are part of the cache key)."""
    try:
        meta = read_metadata(path, _SHAPE_KEYS)
    except (OSError, GGUFError, struct.error):
        return None
    arch = meta.get("general.architecture")

    def get(name: str) -> object:
        return meta.get(f"{arch}.{name}")

    layers = _int(get("block_count"))
    heads = _int(get("attention.head_count"))
    embedding = _int(get("embedding_length"))
    head_dim = embedding // heads if embedding and heads else None
    key = _int(get("attention.key_length")) or head_dim
    value = _int(get("attention.value_length")) or head_dim
    kv_heads = get("attention.head_count_kv")
    if isinstance(kv_heads, list):  # one entry per layer (0 where a layer reuses another's cache)
        per_layer = [n for n in kv_heads if _int(n) is not None]
    elif _int(kv_heads) and layers:
        per_layer = [kv_heads] * layers
    elif heads and layers:  # no grouped-query attention: every head keeps its own keys and values
        per_layer = [heads] * layers
    else:
        return None
    if not key or not value or not per_layer:
        return None
    # The last shared_kv_layers layers reuse earlier layers' caches (Gemma 3n and 4).
    cached = per_layer[: len(per_layer) - (_int(get("attention.shared_kv_layers")) or 0)]
    # A sliding-window layer (pattern true) keeps only its window: in each of llama-server's
    # slots, plus one batch. Without a pattern every layer counts in full, which errs high.
    pattern = get("attention.sliding_window_pattern")
    window = _int(get("attention.sliding_window"))
    swa_key = _int(get("attention.key_length_swa")) or key
    swa_value = _int(get("attention.value_length_swa")) or value
    per_token = fixed = 0
    for i, n in enumerate(cached):
        if window and isinstance(pattern, list) and i < len(pattern) and pattern[i] is True:
            fixed += n * (swa_key + swa_value) * (window * SERVER_SLOTS + 512)
        else:
            per_token += n * (key + value)
    return ModelShape(
        _int(get("context_length")), round(per_token * Q8_0_BYTES), round(fixed * Q8_0_BYTES)
    )
