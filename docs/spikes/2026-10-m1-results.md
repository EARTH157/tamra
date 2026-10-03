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
