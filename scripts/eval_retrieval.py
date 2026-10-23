"""Retrieval eval (spec §12): hit@k on eval/questions.jsonl and the "not found" threshold.

    uv run python scripts/eval_retrieval.py [--rerank [--rerank-candidates N]] [--out results.json]

Needs the bge-m3 model (scripts/fetch_assets.py). Renders eval/corpus into a temporary folder,
indexes it with the real embedder, and runs every question through hybrid search. --rerank
also reorders the top fused candidates with bge-reranker-v2-m3 (fetch_assets.py bge-reranker-*),
as answering does; the "not found" gate stays on the dense similarity.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from build_eval_corpus import build

from tamra.answer import AnswerSettings
from tamra.embedder import MODEL_ID, Embedder
from tamra.ingest.chunker import bge_token_spans
from tamra.ingest.indexer import Indexer
from tamra.reranker import Reranker
from tamra.retriever import best_similarity, fts_query, hybrid_search
from tamra.store import ChunkRecord, Store

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "eval" / "questions.jsonl"
MODEL_DIR = ROOT / ".models" / "bge-m3"
RERANKER_DIR = ROOT / ".models" / "bge-reranker-v2-m3"
RERANK_CANDIDATES = AnswerSettings().rerank_candidates
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


def rerank(
    reranker: Reranker, question: str, chunks: list[ChunkRecord], n: int = RERANK_CANDIDATES
) -> list[ChunkRecord]:
    """The top n chunks reordered by reranker score; the rest keep their fused order."""
    head, tail = chunks[:n], chunks[n:]
    if not head:
        return chunks
    scores = reranker.score(question, [c.text for c in head])
    order = sorted(range(len(head)), key=lambda i: -scores[i])
    return [head[i] for i in order] + tail


def run_eval(
    corpus_dir: Path,
    questions: list[dict],
    model_dir: Path = MODEL_DIR,
    reranker_dir: Path | None = None,
    rerank_candidates: int = RERANK_CANDIDATES,
) -> dict:
    """Index corpus_dir with the real embedder, then retrieve for every question. With
    reranker_dir, each result also gets its rank after reranking the top candidates."""
    embedder = Embedder.load(model_dir)
    reranker = Reranker.load(reranker_dir) if reranker_dir else None
    spans = bge_token_spans(model_dir / "tokenizer.json")
    with tempfile.TemporaryDirectory() as tmp:
        store = Store.open(Path(tmp) / "eval.db")
        try:
            collection = store.create_collection("eval", str(corpus_dir), MODEL_ID)
            indexer = Indexer(store, lambda: embedder, lambda: spans, MODEL_ID)
            indexer.request_reconcile()
            indexer.process()
            files = {f.rel_path: f.status for f in store.list_files(collection.id)}
            results = []
            for q in questions:
                vector = embedder.embed([q["question"]])[0]
                hits = hybrid_search(store, [collection.id], vector, fts_query(q["question"]))
                chunks = store.get_chunks([h.chunk_id for h in hits])
                result = {
                    "id": q["id"],
                    "lang": q["lang"],
                    "answerable": q["file"] is not None,
                    "rank": first_hit_rank(q, chunks) if q["file"] else None,
                    "best_similarity": round(best_similarity(hits), 4),
                }
                if reranker:
                    started = time.perf_counter()
                    reranked = rerank(reranker, q["question"], chunks, rerank_candidates)
                    result["rerank_seconds"] = round(time.perf_counter() - started, 2)
                    result["reranked_rank"] = first_hit_rank(q, reranked) if q["file"] else None
                results.append(result)
        finally:
            store.close()
    report = {**rates(results), "threshold": sweep_thresholds(results)}
    if reranker:
        report["reranked"] = {
            "candidates": rerank_candidates,
            **rates([{**r, "rank": r["reranked_rank"]} for r in results]),
            "seconds_mean": round(sum(r["rerank_seconds"] for r in results) / len(results), 2),
        }
    return {"model_id": MODEL_ID, "files": files, **report, "questions": results}


def rates(results: list[dict]) -> dict:
    languages = sorted({r["lang"] for r in results if r["answerable"]})
    return {
        "hit_rates": hit_rates(results),
        "hit_rates_by_language": {
            lang: hit_rates([r for r in results if r["lang"] == lang]) for lang in languages
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Tamra retrieval eval")
    parser.add_argument("--out", type=Path, help="also write the full results as JSON")
    parser.add_argument("--rerank", action="store_true", help="also score bge-reranker-v2-m3")
    parser.add_argument(
        "--rerank-candidates",
        type=int,
        default=RERANK_CANDIDATES,
        help=f"fused candidates to rerank (default {RERANK_CANDIDATES}, as answering does)",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        corpus_dir = Path(tmp) / "corpus"
        build(corpus_dir)
        report = run_eval(
            corpus_dir,
            load_questions(),
            reranker_dir=RERANKER_DIR if args.rerank else None,
            rerank_candidates=args.rerank_candidates,
        )
    print("files:", report["files"])
    print_summary("fused", report)
    threshold = report["threshold"]
    print(
        f"not-found threshold: recommended {threshold['recommended']}"
        f" (best accuracy {threshold['best_accuracy']:.2f}"
        f" from {threshold['range'][0]} to {threshold['range'][1]})"
    )
    if args.rerank:
        reranked = report["reranked"]
        print_summary(f"reranked top {reranked['candidates']}", reranked)
        print(f"rerank time per question: {reranked['seconds_mean']} s")
    for r in report["questions"]:
        line = f"  {r['id']:<28} rank={r['rank']} best_similarity={r['best_similarity']}"
        if args.rerank:
            line += f" | reranked rank={r['reranked_rank']}"
        print(line)
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def print_summary(title: str, report: dict) -> None:
    print(f"[{title}] all:", " ".join(f"{k}={v:.2f}" for k, v in report["hit_rates"].items()))
    for lang, by_lang in report["hit_rates_by_language"].items():
        print(f"[{title}] {lang}:", " ".join(f"{k}={v:.2f}" for k, v in by_lang.items()))


if __name__ == "__main__":
    sys.exit(main())
