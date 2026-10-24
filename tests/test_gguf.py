import struct

from tamra.models.gguf import read_metadata, read_shape


def _string(text: str) -> bytes:
    data = text.encode()
    return struct.pack("<Q", len(data)) + data


def _kv(key: str, kind: int, value: bytes) -> bytes:
    return _string(key) + struct.pack("<I", kind) + value


def u32(key: str, n: int) -> bytes:
    return _kv(key, 4, struct.pack("<I", n))


def text(key: str, value: str) -> bytes:
    return _kv(key, 8, _string(value))


def int_array(key: str, values: list[int]) -> bytes:
    items = b"".join(struct.pack("<i", v) for v in values)
    return _kv(key, 9, struct.pack("<IQ", 5, len(values)) + items)


def bool_array(key: str, values: list[bool]) -> bytes:
    items = bytes(1 if v else 0 for v in values)
    return _kv(key, 9, struct.pack("<IQ", 7, len(values)) + items)


def string_array(key: str, values: list[str]) -> bytes:
    items = b"".join(_string(v) for v in values)
    return _kv(key, 9, struct.pack("<IQ", 8, len(values)) + items)


def gguf(tmp_path, *pairs: bytes, version: int = 3):
    path = tmp_path / "m.gguf"
    head = b"GGUF" + struct.pack("<IQQ", version, 0, len(pairs))
    path.write_bytes(head + b"".join(pairs) + b"\0" * 64)  # then the tensors, not read
    return path


def qwen3_8b(tmp_path, *extra: bytes):
    return gguf(
        tmp_path,
        text("general.architecture", "qwen3"),
        _kv("general.size", 6, struct.pack("<f", 8.2)),  # a float: skipped
        u32("qwen3.context_length", 40960),
        u32("qwen3.block_count", 36),
        u32("qwen3.embedding_length", 4096),
        u32("qwen3.attention.head_count", 32),
        u32("qwen3.attention.head_count_kv", 8),
        u32("qwen3.attention.key_length", 128),
        u32("qwen3.attention.value_length", 128),
        string_array("tokenizer.ggml.tokens", ["a", "b", "ab"]),
        *extra,
    )


def test_read_shape_of_a_grouped_query_model(tmp_path):
    shape = read_shape(qwen3_8b(tmp_path))
    assert shape is not None
    assert shape.context_length == 40960
    # 36 layers x 8 KV heads x (128 + 128) values, at q8_0's 34 bytes per 32: the real Qwen3-8B
    assert shape.kv_bytes_per_token == 78336


def test_per_layer_kv_heads_and_head_dim_from_the_embedding(tmp_path):
    path = gguf(
        tmp_path,
        text("general.architecture", "gemma"),
        u32("gemma.block_count", 3),
        u32("gemma.embedding_length", 512),
        u32("gemma.attention.head_count", 8),
        int_array("gemma.attention.head_count_kv", [2, 2, 0]),  # the last layer reuses a cache
    )
    shape = read_shape(path)
    assert shape is not None
    assert shape.context_length is None  # not in the file
    assert shape.kv_bytes_per_token == round(4 * (64 + 64) * 34 / 32)


def test_sliding_window_layers_cost_their_window_and_shared_layers_nothing(tmp_path):
    # Gemma 4 E4B: 42 layers, 5 sliding-window layers to each full one, the last 18 sharing.
    pattern = ([True] * 5 + [False]) * 7
    path = gguf(
        tmp_path,
        text("general.architecture", "gemma4"),
        u32("gemma4.context_length", 131072),
        u32("gemma4.block_count", 42),
        u32("gemma4.embedding_length", 2560),
        u32("gemma4.attention.head_count", 8),
        u32("gemma4.attention.head_count_kv", 2),
        u32("gemma4.attention.key_length", 512),
        u32("gemma4.attention.value_length", 512),
        u32("gemma4.attention.key_length_swa", 256),
        u32("gemma4.attention.value_length_swa", 256),
        u32("gemma4.attention.sliding_window", 512),
        bool_array("gemma4.attention.sliding_window_pattern", pattern),
        u32("gemma4.attention.shared_kv_layers", 18),
    )
    shape = read_shape(path)
    assert shape is not None
    # 4 full layers among the first 24: llama.cpp's own buffer grew 204 MiB from 8k to 32k
    assert shape.kv_bytes_per_token == 8704
    assert (24576 * shape.kv_bytes_per_token) / 2**20 == 204
    # 20 window layers x 4 slots: it measured 53 MiB at 8k beside 68 MiB of full layers
    assert round(shape.kv_fixed_bytes / 2**20) == 53


def test_metadata_keeps_strings_and_only_the_integers_asked_for(tmp_path):
    meta = read_metadata(qwen3_8b(tmp_path), {"{arch}.block_count"})
    assert meta["general.architecture"] == "qwen3"
    assert meta["qwen3.block_count"] == 36
    assert "qwen3.context_length" not in meta


def test_a_large_vocabulary_is_skipped_without_asking_the_size_per_token(tmp_path, monkeypatch):
    # A real tokenizer holds ~250k strings: one fstat per read took 15-20 s (GET /api/models).
    vocabulary = [f"t{i}" for i in range(1000)]
    path = qwen3_8b(tmp_path, string_array("tokenizer.ggml.merges", vocabulary))
    import tamra.models.gguf as gguf_module

    calls = []
    real = gguf_module.os.fstat
    monkeypatch.setattr(gguf_module.os, "fstat", lambda fd: calls.append(fd) or real(fd))
    meta = read_metadata(path, {"{arch}.block_count"})
    assert meta["qwen3.block_count"] == 36
    assert len(calls) <= 1


def test_files_that_do_not_tell_give_no_shape(tmp_path):
    assert read_shape(tmp_path / "missing.gguf") is None
    (tmp_path / "junk.gguf").write_bytes(b"gguf")
    assert read_shape(tmp_path / "junk.gguf") is None
    truncated = tmp_path / "cut.gguf"
    truncated.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 5) + _string("general.arch"))
    assert read_shape(truncated) is None
    huge = tmp_path / "huge.gguf"  # a damaged length must not be allocated
    huge.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", 2**60))
    assert read_shape(huge) is None
    assert read_shape(gguf(tmp_path, text("general.architecture", "x"))) is None  # no sizes


def test_a_changed_file_is_read_again(tmp_path):
    path = qwen3_8b(tmp_path)
    assert read_shape(path).context_length == 40960
    gguf(tmp_path, text("general.architecture", "x"), u32("x.context_length", 4096))
    assert read_shape(path) is None  # the new file has no sizes: not the cached answer
