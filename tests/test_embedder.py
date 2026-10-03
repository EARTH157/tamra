import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from tamra.embedder import MODEL_ID, Embedder

ROOT = Path(__file__).resolve().parents[1]
VOCAB = {"<s>": 0, "<pad>": 1, "<unk>": 3, "hello": 5, "world": 6, "tamra": 7}


def tiny_tokenizer() -> Tokenizer:
    tokenizer = Tokenizer(models.WordLevel(VOCAB, unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    return tokenizer


class FakeSession:
    """Stands in for onnxruntime: each text's CLS row encodes its first token id."""

    def __init__(self, inputs=("input_ids", "attention_mask")):
        self.inputs = [SimpleNamespace(name=name) for name in inputs]
        self.feeds: list[dict] = []

    def get_inputs(self):
        return self.inputs

    def run(self, output_names, feeds):
        self.feeds.append(feeds)
        ids = feeds["input_ids"]
        hidden = np.zeros((*ids.shape, 1024), dtype=np.float32)
        hidden[:, 0, 0] = 3.0
        hidden[:, 0, 1] = 4.0 * ids[:, 0]
        return [hidden]


def test_batches_keep_the_input_order_and_rows_are_normalised():
    session = FakeSession()
    vectors = Embedder(tiny_tokenizer(), session).embed(
        ["hello", "world", "tamra", "hello world", "world"], batch_size=2
    )
    assert vectors.shape == (5, 1024)
    assert vectors.dtype == np.float32
    assert [len(feeds["input_ids"]) for feeds in session.feeds] == [2, 2, 1]
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-6)
    np.testing.assert_allclose(
        vectors[:, 1] / vectors[:, 0], [20 / 3, 24 / 3, 28 / 3, 20 / 3, 24 / 3], rtol=1e-5
    )


def test_a_batch_is_padded_and_masked():
    session = FakeSession()
    Embedder(tiny_tokenizer(), session).embed(["hello", "hello world tamra"])
    feeds = session.feeds[0]
    assert feeds["input_ids"].tolist() == [[5, 1, 1], [5, 6, 7]]
    assert feeds["attention_mask"].tolist() == [[1, 0, 0], [1, 1, 1]]
    assert "token_type_ids" not in feeds


def test_token_type_ids_are_sent_when_the_model_takes_them():
    session = FakeSession(inputs=("input_ids", "attention_mask", "token_type_ids"))
    Embedder(tiny_tokenizer(), session).embed(["hello world"])
    assert session.feeds[0]["token_type_ids"].tolist() == [[0, 0]]


def test_long_texts_are_truncated_to_max_length():
    session = FakeSession()
    Embedder(tiny_tokenizer(), session, max_length=2).embed(["hello world tamra"])
    assert session.feeds[0]["input_ids"].tolist() == [[5, 6]]


def test_no_texts_need_no_model_call():
    session = FakeSession()
    assert Embedder(tiny_tokenizer(), session).embed([]).shape == (0, 1024)
    assert session.feeds == []


def test_batch_size_must_be_positive():
    with pytest.raises(ValueError, match="batch_size"):
        Embedder(tiny_tokenizer(), FakeSession()).embed(["hello"], batch_size=0)


def test_load_names_the_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="tokenizer.json"):
        Embedder.load(tmp_path)
    tiny_tokenizer().save(str(tmp_path / "tokenizer.json"))
    with pytest.raises(FileNotFoundError, match="model.onnx"):
        Embedder.load(tmp_path)


def test_model_id_names_the_pinned_export():
    assets = json.loads((ROOT / "scripts" / "assets.json").read_text(encoding="utf-8"))
    revision = MODEL_ID.rsplit(":", 1)[1]
    assert f"/Xenova/bge-m3/resolve/{revision}" in assets["bge-m3-model"]["url"]


@pytest.fixture(scope="module")
def embedder(bge_dir):
    return Embedder.load(bge_dir)


@pytest.mark.assets
def test_shape_dtype_and_normalisation(embedder):
    vecs = embedder.embed(["hello", "สวัสดี", "你好"])
    assert vecs.shape == (3, 1024)
    assert vecs.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-4)


@pytest.mark.assets
def test_cross_lingual_similarity(embedder):
    th, en_same, zh_same, en_other = embedder.embed(
        [
            "แมวกำลังนอนหลับอยู่บนโซฟา",
            "A cat is sleeping on the sofa",
            "猫正在沙发上睡觉",
            "The stock market fell sharply today",
        ]
    )
    assert th @ en_same > th @ en_other + 0.1
    assert th @ zh_same > th @ en_other + 0.1


@pytest.mark.assets
def test_padding_changes_an_embedding_only_slightly(embedder):
    # The int8 export is dynamically quantized, so a vector depends a little on its batch
    # (M0 measured cosine ~0.989; the roadmap accepts this and lets the eval judge retrieval).
    short = "Tamra answers questions about documents."
    alone = embedder.embed([short])[0]
    batched = embedder.embed([short, short + " " + "padding forces longer batch. " * 20])[0]
    assert float(alone @ batched) >= 0.98
