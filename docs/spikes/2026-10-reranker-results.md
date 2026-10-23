# Reranker results

## Retrieval eval

- Corpus and questions: the M1 eval set (`eval/corpus`, `eval/questions.jsonl`; 24 answerable,
  4 off-topic). See `2026-10-m1-results.md`.
- Reranker: `bge-reranker-v2-m3` int8 ONNX export,
  `onnx-community/bge-reranker-v2-m3-ONNX@6f5ff652` (`onnx/model_int8.onnx`, Apache-2.0), on
  onnxruntime's CPU provider. It scores (question, chunk) pairs and reorders the top fused
  candidates. Command: `uv run python scripts/eval_retrieval.py --rerank [--rerank-candidates N]`.

| Questions | hit@1 | hit@4 | hit@10 |
|---|---|---|---|
| all (24), fused | 0.67 | 0.92 | 1.00 |
| all (24), reranked top 10, 15 or 30 | 1.00 | 1.00 | 1.00 |
| th (8), fused | 0.62 | 0.75 | 1.00 |
| th (8), reranked | 1.00 | 1.00 | 1.00 |
| en (9), fused | 0.44 | 1.00 | 1.00 |
| en (9), reranked | 1.00 | 1.00 | 1.00 |
| zh (7), fused and reranked | 1.00 | 1.00 | 1.00 |

The fused numbers equal the M1 run. The two cross-language misses (`th-lease-notice` and
`th-security-mfa-app`, both at fused rank 5) and the six questions at fused rank 4 all move
to rank 1. Every expected chunk is in the fused top 10, so reranking 10 candidates finds the
same hits as 15 or 30.

**"Not found" gate.** The reranker score separates the two groups (lowest answerable 0.0073,
highest off-topic 0.0006), but the gap is under 0.01 in absolute terms, so the gate stays on
the dense similarity (0.43). The reranker only reorders.

## Latency

The cost grows with the passage length. Measured with `Reranker.score` on this dev machine's
CPU, after a warm-up call:

| Candidates | ~450-token chunks | ~40-token chunks |
|---|---|---|
| 4 | 1.6 s | 0.10 s |
| 10 | 4.2 s | 0.25 s |
| 15 | 6.7 s | 0.36 s |
| 30 | 13.2 s | 0.71 s |

The eval corpus has short chunks (1.4-1.5 s per question at any candidate count), so it
understates the cost for real documents, whose chunks are mostly full (~450 tokens). A batch
size of 8 is slightly faster than 16. `AnswerSettings.rerank_candidates` is 10: it keeps
every eval hit and costs about 4 s per question in the worst case.

The reranker is about 570 MB on disk and in memory. It is loaded on the first question after
it is installed, and kept.
