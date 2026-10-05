"""Attribution eval (spec §7): does a selection get matched to the right saved source?

    uv run python scripts/eval_attribution.py [--out results.json]

Needs the bge-m3 model (scripts/fetch_assets.py). Runs every case in eval/attribution.jsonl
through `tamra.attribution.Attributor` with the real embedder and prints each case's best
score and label. Cases have a `group`:

- main (the default): the set the constants are calibrated on; the accuracy bar applies here.
- heldout: cases written after calibration; reported separately, never tuned on.
- hard_negative: same-topic claims the sources do not support, or contradict. The matcher
  measures similar wording, not truth, so these are reported as a rejection rate only.
"""

import argparse
import json
import sys
from pathlib import Path

from tamra import attribution
from tamra.attribution import Attributor
from tamra.embedder import Embedder
from tamra.ingest.chunker import bge_token_spans
from tamra.store import MessageRecord, SourceRecord

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "eval" / "attribution.jsonl"
MODEL_DIR = ROOT / ".models" / "bge-m3"
GROUPS = ("main", "heldout", "hard_negative")


def load_cases(path: Path = CASES) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def to_message(case: dict, message_id: int) -> MessageRecord:
    sources = tuple(
        SourceRecord(
            n=s["n"],
            chunk_id=None,
            file_id=None,
            rel_path=f"source{s['n']}",
            text=s["text"],
            location={},
            file_hash=None,
        )
        for s in case["sources"]
    )
    return MessageRecord(
        id=message_id,
        role="assistant",
        content=case.get("content", ""),
        provider=None,
        model=None,
        created_at="",
        sources=sources,
    )


def summarise(rows: list[dict]) -> dict:
    """Accuracy of the expected source and label, and the cases wrongly matched or rejected."""
    answerable = [r for r in rows if r["expect"] != "none"]
    unanswerable = [r for r in rows if r["expect"] == "none"]
    return {
        "cases": len(rows),
        "n_accuracy": (
            sum(r["got_n"] == r["expect_n"] for r in answerable) / len(answerable)
            if answerable
            else None
        ),
        "label_accuracy": sum(r["got"] == r["expect"] for r in rows) / len(rows) if rows else None,
        "rejection_rate": (
            sum(r["got"] == "none" for r in unanswerable) / len(unanswerable)
            if unanswerable
            else None
        ),
        "false_matches": [r["id"] for r in unanswerable if r["got"] != "none"],
        "wrong_source": [r["id"] for r in answerable if r["got_n"] != r["expect_n"]],
        "strong_on_wrong_source": [
            r["id"] for r in answerable if r["got"] == "strong" and r["got_n"] != r["expect_n"]
        ],
    }


def run_cases(attributor: Attributor, cases: list[dict]) -> dict:
    """Run every case. The top-level accuracy fields are for the main group only."""
    rows = []
    for message_id, case in enumerate(cases, start=1):
        message = to_message(case, message_id)
        matches = attributor.attribute(message, case["selection"], case.get("only"))
        top = matches[0] if matches else None
        rows.append(
            {
                "id": case["id"],
                "group": case.get("group", "main"),
                "expect_n": case["expect_n"],
                "expect": case["expect"],
                "got_n": top.n if top else None,
                "got": top.label if top else "none",
                "score": round(top.score, 3) if top else None,
                "all": [(m.n, m.label, round(m.score, 3)) for m in matches],
            }
        )
    groups = {g: summarise([r for r in rows if r["group"] == g]) for g in GROUPS}
    return {"rows": rows, "groups": groups, **groups["main"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Tamra attribution eval")
    parser.add_argument("--out", type=Path, help="also write the full results as JSON")
    args = parser.parse_args()
    embedder = Embedder.load(MODEL_DIR)
    spans = bge_token_spans(MODEL_DIR / "tokenizer.json")
    report = run_cases(Attributor(embedder.embed, spans), load_cases())
    print(
        f"constants: TRIGRAM_WEIGHT={attribution.TRIGRAM_WEIGHT}"
        f" NUMBER_PENALTY={attribution.NUMBER_PENALTY}"
        f" STRONG={attribution.STRONG} PARTIAL={attribution.PARTIAL}"
    )
    print(f"{'id':<26} {'group':<13} {'expect':<10} {'got':<10} score  matches")
    for r in report["rows"]:
        want = f"{r['expect']}/{r['expect_n']}"
        got = f"{r['got']}/{r['got_n']}"
        ok = "ok  " if (r["got_n"], r["got"]) == (r["expect_n"], r["expect"]) else "MISS"
        print(f"{r['id']:<26} {r['group']:<13} {want:<10} {got:<10} {ok} {r['score']}  {r['all']}")
    for name, g in report["groups"].items():
        if not g["cases"]:
            continue
        print(f"\n[{name}] {g['cases']} cases")
        if g["n_accuracy"] is not None:
            print(f"  expect_n accuracy (answerable): {g['n_accuracy']:.3f}")
        print(f"  expect (label) accuracy: {g['label_accuracy']:.3f}")
        if g["rejection_rate"] is not None:
            print(f"  rejection rate (none cases): {g['rejection_rate']:.3f}")
        print(f"  false matches: {g['false_matches']}")
        print(f"  wrong source: {g['wrong_source']}")
        print(f"  strong for the wrong source: {g['strong_on_wrong_source']}")
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
