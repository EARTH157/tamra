# Tamra v1 roadmap — design

**Date:** 2026-10-03
**Status:** Approved by the project owner in brainstorming (2026-10-03)
**Builds on:** `docs/superpowers/specs/2026-10-02-tamra-design.md` (the behavior spec; it stays the
source of truth) and `docs/spikes/2026-10-m0-results.md` (M0 results, risks, deferred findings).

## Goal

Deliver Tamra v1: milestones M1–M6 of the design spec (§13), ending with a Windows installer
published from a version tag.

## Process

- Milestones run in spec order: M1 → M2 → M3 → M4 → M5 → M6. Each depends on the one before it.
- Each milestone gets its own implementation plan, written when the milestone starts so it can use
  what the previous milestone taught: `docs/superpowers/plans/<date>-tamra-m<N>-<topic>.md`.
- Each plan is executed task by task with a review per task and a final whole-branch review, on a
  branch `m<N>-<topic>`, then merged to `main` through a pull request once CI is green and the final
  review is clean (merge commit, history kept).
- Work continues from one milestone to the next without stopping, except for: large downloads,
  publishing a release or installer, and other irreversible or outward-facing actions. Those wait
  for the owner.

## Milestones

| # | Scope (from spec §13, plus carried items) | Exit criterion |
|---|---|---|
| M1 | Core loop: one collection, PDF/DOCX/TXT/MD ingestion with watcher and reconcile, chunking, hybrid search (dense + FTS5 trigram, RRF), local LLM answers with `[n]` citations over SSE, chats persisted, chat UI with source chips; eval set and hit@k script; the security and robustness items the M0 reviews deferred to M1 (spike report, "Deferred review findings by milestone") | In `Tamra.exe`, pick a folder of mixed TH/EN/ZH documents, ask a question, and get a cited answer |
| M2 | API providers (Anthropic, OpenAI-compatible), settings UI, model catalog with download (resumable) and import, hardware tiers, LLM picks per tier from the eval set, plus the M2 items in the spike report | Switch local ↔ API and see both work; install a model offline via import |
| M3 | Attribution popup and document viewer with highlights | Select text in an answer and see its source highlighted in the PDF |
| M4 | Multiple collections per chat and the chat history UI | Create two collections and query either or both |
| M5 | OCR: engine spike (Tesseract, RapidOCR, Windows OCR), then integration | A scanned Thai PDF becomes searchable and highlightable |
| M6 | Inno Setup installer and the release workflow, plus the M6 items in the spike report | Tagging a version publishes a working installer |

## Decisions

1. **Eval corpus:** a small set of synthetic documents written for this project in Thai, English,
   and Chinese (no third-party content, so no licensing questions), with `eval/questions.jsonl`
   giving each question's expected file and page, and a script that reports hit@k. It is created in
   M1 and tunes the §6 "no relevant results" threshold; M2 extends it to pick LLMs per tier.
2. **int8 embedding batch noise** (spike risk 2): accepted for now. The padding test becomes a
   cosine bound (≥ 0.98) instead of an element-wise tolerance, and M1 checks retrieval on the eval
   set. If hit@k suffers, revisit (one passage per call, or an fp16/fp32 export).
3. **Development LLM through M1:** the pinned Qwen2.5-0.5B (owner's choice). Thai answer quality
   will be limited until M2 picks real models per tier; M1 is judged on the mechanics (retrieval,
   citations, persistence, UI).
4. **M1 absorbs the deferred security items** before it adds real data endpoints: Host allowlist
   (DNS rebinding), an ASGI token gate that also covers WebSocket scopes and SSE, the UI reading its
   token once, llama-server with a per-launch `--api-key` inside a Job Object, and a file log.

## Risks

- Thai and Chinese answer quality with the 0.5B model during M1 (mitigated in M2).
- Large model downloads over unreliable connections (resumable downloads land in M2).
- OCR quality for Thai scans is unknown until the M5 spike.
