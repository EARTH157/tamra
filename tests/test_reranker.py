from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from eval_retrieval import RERANKER_DIR, rerank
from tokenizers import Tokenizer, models, pre_tokenizers

from tamra.reranker import Reranker
from tamra.store import ChunkRecord

VOCAB = {"<s>": 0, "<pad>": 1, "<unk>": 3, "hello": 5, "world": 6, "tamra": 7}


def tiny_tokenizer() -> Tokenizer:
    tokenizer = Tokenizer(models.WordLevel(VOCAB, unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    return tokenizer


class FakeSession:
    """Stands in for onnxruntime: a pair's logit is its last real token id minus 6."""

    def __init__(self):
        self.feeds: list[dict] = []

    def get_inputs(self):
        return [SimpleNamespace(name="input_ids"), SimpleNamespace(name="attention_mask")]

    def run(self, output_names, feeds):
        self.feeds.append(feeds)
        last = feeds["attention_mask"].sum(axis=1) - 1
        ids = feeds["input_ids"][np.arange(len(last)), last]
        return [(ids - 6).astype(np.float32)[:, None]]


def test_scores_are_sigmoids_in_passage_order_across_batches():
    session = FakeSession()
    scores = Reranker(tiny_tokenizer(), session).score(
        "hello", ["tamra", "world", "hello", "tamra"], batch_size=3
    )
    assert [len(f["input_ids"]) for f in session.feeds] == [3, 1]
    np.testing.assert_allclose(
        scores, 1 / (1 + np.exp(-np.array([1.0, 0.0, -1.0, 1.0]))), rtol=1e-6
    )


def test_no_passages_gives_no_scores():
    assert Reranker(tiny_tokenizer(), FakeSession()).score("hello", []).shape == (0,)


def chunk(chunk_id: int, text: str) -> ChunkRecord:
    return ChunkRecord(chunk_id, 1, "a.md", 0, text, {"kind": "text"}, None)


def test_rerank_reorders_only_the_top_n():
    reranker = Reranker(tiny_tokenizer(), FakeSession())
    chunks = [chunk(1, "hello"), chunk(2, "tamra"), chunk(3, "world"), chunk(4, "hello")]
    assert [c.id for c in rerank(reranker, "hello", chunks, n=3)] == [2, 3, 1, 4]


@pytest.mark.assets
def test_the_real_reranker_prefers_the_relevant_cross_language_passage():
    scores = Reranker.load(Path(RERANKER_DIR)).score(
        "ต้องแจ้งล่วงหน้ากี่วันก่อนยกเลิกสัญญาเช่า",
        ["The canteen opens at 7:30.", "The tenant must give 60 days notice to end the lease."],
    )
    assert scores[1] > 0.5 > scores[0]
