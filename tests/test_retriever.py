import numpy as np
import pytest
from fakes import FakeEmbedder

from tamra.retriever import Hit, best_similarity, fts_query, hybrid_search, query_text
from tamra.store import ChunkInput, Store


def test_query_text_adds_the_previous_question():
    assert query_text("and item 2?", None) == "and item 2?"
    assert query_text("and item 2?", "list the fees") == "list the fees\nand item 2?"


def test_fts_query_quotes_unique_trigrams():
    assert fts_query("Lease lease") == '"lea" OR "eas" OR "ase"'
    assert fts_query('say "hello"') == '"say" OR "hel" OR "ell" OR "llo"'
    assert fts_query("a ab, 12") is None


def test_fts_query_keeps_thai_marks_inside_words():
    grams = fts_query("เช่าบ้าน").split(" OR ")
    assert len(grams) == 6
    assert grams[0] == '"เช่"'


def test_fts_query_is_capped():
    assert fts_query("abcdefghijklmnopqrstuvwxyz" * 5, max_terms=10).count(" OR ") == 9


class _FakeStore:
    def __init__(self, dense, keyword):
        self.dense, self.keyword = dense, keyword

    def search_dense(self, collection_id, vector, k):
        return self.dense

    def search_keyword(self, collection_id, fts, k):
        if self.keyword is None:
            raise AssertionError("keyword search must not run without a query")
        return self.keyword


def test_rank_fusion_rewards_chunks_found_both_ways():
    store = _FakeStore(dense=[(1, 0.9), (2, 0.5)], keyword=[3, 1])
    hits = hybrid_search(store, 1, np.zeros(1024, dtype=np.float32), '"abc"')
    assert [h.chunk_id for h in hits] == [1, 3, 2]
    assert hits[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert (hits[1].similarity, hits[2].similarity) == (None, 0.5)


def test_without_a_keyword_query_only_dense_results_count():
    store = _FakeStore(dense=[(5, 0.7)], keyword=None)
    hits = hybrid_search(store, 1, np.zeros(1024, dtype=np.float32), None)
    assert [(h.chunk_id, h.similarity) for h in hits] == [(5, 0.7)]


def test_best_similarity():
    assert best_similarity([]) == 0.0
    assert best_similarity([Hit(1, 0.1, None), Hit(2, 0.05, 0.42), Hit(3, 0.02, 0.61)]) == 0.61


def test_hybrid_search_finds_the_matching_chunk_in_a_real_store(tmp_path):
    store = Store.open(tmp_path / "t.db")
    collection = store.replace_collection("Docs", "C:/docs", "m")
    embedder = FakeEmbedder()
    texts = [
        "The lease term is three years",
        "Parking costs fifty baht",
        "Pets are welcome in the garden",
    ]
    file_id = store.add_file(collection.id, "a.md", 1, 1.0)
    chunks = [
        ChunkInput(t, {"kind": "text", "line_start": i + 1, "line_end": i + 1})
        for i, t in enumerate(texts)
    ]
    store.replace_file_chunks(file_id, chunks, embedder.embed(texts), content_hash="h", note=None)
    question = "how long is the lease term"
    hits = hybrid_search(store, collection.id, embedder.embed([question])[0], fts_query(question))
    assert store.get_chunks([hits[0].chunk_id])[0].text == "The lease term is three years"
    assert hits[0].similarity > 0.3
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    store.close()
