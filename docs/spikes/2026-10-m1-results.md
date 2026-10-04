# M1 results

## Retrieval eval

- Corpus: `eval/corpus`, 10 documents (Thai, English, and Chinese; PDF, DOCX, Markdown, UTF-8
  text, and a TIS-620 text file), 2 of them distractors with no questions.
- Questions: `eval/questions.jsonl`, 24 answerable (many cross-language) and 4 off-topic.
- Embedding model: `bge-m3-int8@Xenova/bge-m3:4de13258`. Command:
  `uv run python scripts/eval_retrieval.py`.

| Questions | hit@1 | hit@4 | hit@10 |
|---|---|---|---|
| all (24) | 0.67 | 0.92 | 1.00 |
| th (8) | 0.62 | 0.75 | 1.00 |
| en (9) | 0.44 | 1.00 | 1.00 |
| zh (7) | 1.00 | 1.00 | 1.00 |

All 10 files were indexed. hit@k counts a question as a hit when a chunk from the expected file
(and the expected page, for PDFs) is among the top k fused results.

**"Not found" threshold.** The sweep over 0.20 to 0.80 reaches a best accuracy of 1.00 for
thresholds from 0.41 to 0.48; its recommendation (the middle of that range) is 0.45. The lowest
best similarity among answerable questions is 0.4825 (`zh-purifier-child-lock`), and the highest
among off-topic questions is 0.4089 (`none-weather`). The value set in
`AnswerSettings.min_similarity` is `min(0.45, round(0.4825 - 0.05, 2))` = 0.43. The margin below
the lowest answerable question keeps answerable questions like these from being refused. The
gap between the two groups is narrow (0.07), so an off-topic question may slip through; the
prompt rule "if the sources do not answer the question, say so plainly" covers that case.

**Misses.** Two answerable questions had their expected file outside the top 4, both Thai:
`th-lease-notice` (rank 5, English lease PDF) and `th-security-mfa-app` (rank 5, English
Markdown policy). Both are cross-language questions and are found within the top 10. Six more
questions hit at exactly rank 4 (`en-leave-maternity`, `en-warranty-mattress`,
`en-travel-mileage`, `th-purifier-noise`, `en-canteen-hours`, `en-shipping-custom-return`), so
the margin at hit@4 is thin.

## Exit criterion

`dist/Tamra/Tamra.exe` was built with `./scripts/build.ps1` (run under Windows PowerShell 5.1) and
checked on the dev machine (`scripts/exe_smoke.py` and the packaged selfcheck). Models: bge-m3
int8 and Qwen2.5-0.5B-instruct Q4_K_M; the system prompt includes the example citation line.

**Packaged selfcheck** (exit code 0; `sqlite`, `documents`, `embedding`, and `llm` all `ok`):

| Measure | Value |
|---|---|
| Embedding (packaged) | 5.09 passages/s, model load 2.0 s |
| Local LLM start | 4.78 s, first token 0.03 s |
| Local LLM speed | 255.45 tokens/s (64 tokens) |
| GPU used (Vulkan) | yes |

**Smoke test** (the exe in `--dev` mode, scratch data folder, the 10-file eval corpus; exit
code 0, no problems, 3 of 3 answerable questions cited):

| Step | Seconds |
|---|---|
| Startup to healthy API | 2.0 |
| Indexing 10 files (10 indexed, 0 failed, 0 skipped) | 4.0 |
| Thai question | 4.9 (includes the first model use) |
| English question | 0.2 |
| Chinese question | 0.2 |
| Off-topic question | 0.0 (the not-found reply never calls the model) |
| Folder watcher indexes a new file | 2.5 |

| Question | Answer | Sources | Cited `[n]` |
|---|---|---|---|
| `th-leave-annual` | พนักงานที่ผ่านการทดลองงานแล้วมีสิทธิลาพักร้อนปีละ 12 วัน [1] | hr-leave-policy.docx, travel-expenses.pdf, canteen-rules.txt, company-history.txt | yes |
| `en-lease-rent` | The monthly rent is 18,500 baht, and it is due on the 5th day of each month. [1] | apartment-lease.pdf, meeting-notes-2026-09.md, it-security-policy.md, canteen-rules.txt | yes |
| `zh-warranty-sofa` | 沙发框架保修 [1] 五年。 | furniture-warranty.txt, apartment-lease.pdf, shipping-policy.pdf, it-security-policy.md | yes |
| `none-world-cup` | Not found in the documents. | none | n/a |

Each answerable question had its expected file among the sources, the answer and its sources were
saved in the chat, and the off-topic question got the not-found reply with no sources.
All answers were correct and short.

The final-review fix wave (language detection, batched embedding with a stop check, exit
and startup fixes, stable ids) rebuilt the exe and re-ran the selfcheck and the smoke test; the
numbers above are from that re-run.

**Automatic citation.** The dev model rarely writes `[n]` once it sees real passages: probes
found 0 of 5 answers cited with the plan's prompt and 1 of 5 with an extra reminder. The answer
service now appends `[n]` when an uncited answer shares at least half of its character trigrams
with one source (`AUTO_CITE_MIN_OVERLAP = 0.5`). All three cited answers above got their marker
this way. Larger models in M2 are expected to cite on their own, and then the fallback rarely
fires.

**Process cleanup.** The exe was ended with a hard kill by PID; no `llama-server.exe` started by
the run was left afterwards (the Job Object works).
