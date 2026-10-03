"""Hybrid retrieval (spec §6): dense KNN plus FTS5 trigram keywords, fused with RRF."""

from dataclasses import dataclass

import numpy as np

from tamra.store import Store

RRF_K = 60
CANDIDATES = 30
MAX_FTS_TERMS = 64


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    score: float  # Reciprocal Rank Fusion score
    similarity: float | None  # cosine similarity from the dense search; None if keyword-only


def query_text(question: str, previous_question: str | None) -> str:
    """The latest question plus the previous one, so short follow-ups stay on topic."""
    if not previous_question:
        return question
    return f"{previous_question}\n{question}"


def fts_query(text: str, max_terms: int = MAX_FTS_TERMS) -> str | None:
    """OR the text's character trigrams; None when no word reaches 3 characters.

    The trigram index matches any 3-character substring and BM25 weighs rare trigrams more,
    so this also works for Thai and Chinese text, which has no spaces between words.
    """
    grams: list[str] = []
    seen: set[str] = set()
    for word in _words(text):
        for i in range(len(word) - 2):
            gram = word[i : i + 3]
            if gram not in seen:
                seen.add(gram)
                grams.append(gram)
    if not grams:
        return None
    return " OR ".join('"' + gram.replace('"', '""') + '"' for gram in grams[:max_terms])


def _words(text: str) -> list[str]:
    """Runs of letters, digits, and Thai characters (vowel and tone marks included)."""
    words: list[str] = []
    current: list[str] = []
    for char in text.lower():
        if char.isalnum() or 0x0E00 <= ord(char) <= 0x0E7F:
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def hybrid_search(
    store: Store, collection_id: int, vector: np.ndarray, fts: str | None, k: int = CANDIDATES
) -> list[Hit]:
    """Dense and keyword candidates fused by Reciprocal Rank Fusion, best first."""
    dense = store.search_dense(collection_id, vector, k)
    keyword = store.search_keyword(collection_id, fts, k) if fts else []
    similarity = dict(dense)
    scores: dict[int, float] = {}
    for rank, (chunk_id, _) in enumerate(dense, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    for rank, chunk_id in enumerate(keyword, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    hits = [Hit(chunk_id, score, similarity.get(chunk_id)) for chunk_id, score in scores.items()]
    return sorted(hits, key=lambda hit: (-hit.score, hit.chunk_id))


def best_similarity(hits: list[Hit]) -> float:
    return max((h.similarity for h in hits if h.similarity is not None), default=0.0)
