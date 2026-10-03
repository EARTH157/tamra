"""Retrieval eval (spec §12): hit@k on eval/questions.jsonl and the "not found" threshold.

    uv run python scripts/eval_retrieval.py [--out results.json]

Needs the bge-m3 model (scripts/fetch_assets.py). Renders eval/corpus into a temporary folder,
indexes it with the real embedder, and runs every question through hybrid search.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from build_eval_corpus import build

from tamra.embedder import MODEL_ID, Embedder
from tamra.ingest.chunker import bge_token_spans
from tamra.ingest.indexer import Indexer
from tamra.retriever import best_similarity, fts_query, hybrid_search
from tamra.store import ChunkRecord, Store

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "questions.jsonl"
MODEL_DIR = ROOT / ".models" / "bge-m3"
K_VALUES = (1, 4, 10)


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def is_hit(question: dict, chunk: ChunkRecord) -> bool:
    """The chunk comes from the expected file and covers the expected page, if one is given."""
    if chunk.rel_path != question["file"]:
        return False
    page = question.get("page")
    if page is None:
        return True
    return chunk.location.get("page_start", 0) <= page <= chunk.location.get("page_end", 0)


def first_hit_rank(question: dict, chunks: list[ChunkRecord]) -> int | None:
    for rank, chunk in enumerate(chunks, start=1):
        if is_hit(question, chunk):
            return rank
    return None


def hit_rates(results: list[dict], ks: tuple[int, ...] = K_VALUES) -> dict[str, float]:
    """hit@k over the answerable questions."""
    answerable = [r for r in results if r["answerable"]]
    return {
        f"hit@{k}": sum(1 for r in answerable if r["rank"] is not None and r["rank"] <= k)
        / len(answerable)
        for k in ks
    }


def sweep_thresholds(
    results: list[dict], low: float = 0.20, high: float = 0.80, step: float = 0.01
) -> dict:
    """Accuracy of the "not found" gate (answer when best similarity >= t) for each t.

    Returns the best accuracy, the lowest and highest threshold reaching it, and the middle
    one of those thresholds as the recommendation.
    """
    scored = []
    for i in range(round((high - low) / step) + 1):
        t = round(low + i * step, 2)
        correct = sum(1 for r in results if (r["best_similarity"] >= t) == r["answerable"])
        scored.append((t, correct / len(results)))
    best = max(accuracy for _, accuracy in scored)
    winners = [t for t, accuracy in scored if accuracy == best]
    return {
        "best_accuracy": best,
        "range": [winners[0], winners[-1]],
        "recommended": winners[len(winners) // 2],
    }


def run_eval(corpus_dir: Path, questions: list[dict], model_dir: Path = MODEL_DIR) -> dict:
    """Index corpus_dir with the real embedder, then retrieve for every question."""
    embedder = Embedder.load(model_dir)
    spans = bge_token_spans(model_dir / "tokenizer.json")
    with tempfile.TemporaryDirectory() as tmp:
        store = Store.open(Path(tmp) / "eval.db")
        try:
            collection = store.replace_collection("eval", str(corpus_dir), MODEL_ID)
            indexer = Indexer(store, lambda: embedder, lambda: spans, MODEL_ID)
            indexer.request_reconcile()
            indexer.process()
            files = {f.rel_path: f.status for f in store.list_files(collection.id)}
            results = []
            for q in questions:
                vector = embedder.embed([q["question"]])[0]
                hits = hybrid_search(store, collection.id, vector, fts_query(q["question"]))
                chunks = store.get_chunks([h.chunk_id for h in hits])
                results.append(
                    {
                        "id": q["id"],
                        "lang": q["lang"],
                        "answerable": q["file"] is not None,
                        "rank": first_hit_rank(q, chunks) if q["file"] else None,
                        "best_similarity": round(best_similarity(hits), 4),
                    }
                )
        finally:
            store.close()
    languages = sorted({r["lang"] for r in results if r["answerable"]})
    return {
        "model_id": MODEL_ID,
        "files": files,
        "hit_rates": hit_rates(results),
        "hit_rates_by_language": {
            lang: hit_rates([r for r in results if r["lang"] == lang]) for lang in languages
        },
        "threshold": sweep_thresholds(results),
        "questions": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Tamra retrieval eval")
    parser.add_argument("--out", type=Path, help="also write the full results as JSON")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        corpus_dir = Path(tmp) / "corpus"
        build(corpus_dir)
        report = run_eval(corpus_dir, load_questions())
    print("files:", report["files"])
    print("all:", " ".join(f"{k}={v:.2f}" for k, v in report["hit_rates"].items()))
    for lang, rates in report["hit_rates_by_language"].items():
        print(f"{lang}:", " ".join(f"{k}={v:.2f}" for k, v in rates.items()))
    threshold = report["threshold"]
    print(
        f"not-found threshold: recommended {threshold['recommended']}"
        f" (best accuracy {threshold['best_accuracy']:.2f}"
        f" from {threshold['range'][0]} to {threshold['range'][1]})"
    )
    for r in report["questions"]:
        print(f"  {r['id']:<28} rank={r['rank']} best_similarity={r['best_similarity']}")
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
