"""End-to-end check of the packaged app through its API (the M1 exit criterion).

    uv run python scripts/exe_smoke.py [--exe dist/Tamra/Tamra.exe] [--out report.json]

Starts Tamra.exe in dev mode (API only on 127.0.0.1:8765, token "dev") with a scratch data
folder and the repo's models, indexes the eval corpus, asks one question in each language and
one off-topic question, checks the streamed answers and the saved chats, adds a file to check
the folder watcher, then ends the exe by PID and checks that its llama-server ended with it.

The local model is whatever Tamra picks from .models: an installed catalog model if there is
one, else the uncatalogued dev GGUF that fetch_assets.py downloads (qwen2.5-0.5b-instruct).
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from build_eval_corpus import build
from eval_retrieval import load_questions

from tamra.answer import NOT_FOUND

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
SMOKE_IDS = ("th-leave-annual", "en-lease-rent", "zh-warranty-sofa", "none-world-cup")
CITATION = re.compile(r"\[(\d+)\]")


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def llama_server_pids() -> set[int]:
    """PIDs of running llama-server.exe processes (read only: nothing is stopped by name)."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    pids = set()
    for line in out.splitlines():
        fields = [field.strip('"') for field in line.split('","')]
        if len(fields) > 1 and fields[1].isdigit():
            pids.add(int(fields[1]))
    return pids


def wait_for(check, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {what}")


def healthy(client: httpx.Client) -> bool:
    try:
        return client.get("/api/health").status_code == 200
    except httpx.TransportError:
        return False


def settled(client: httpx.Client, total: int) -> dict | None:
    """The index status once every file has been handled, else None."""
    index = client.get("/api/collection").json()["index"]
    if index is None:
        return None
    if index["error"]:
        raise SystemExit(f"indexing stopped: {index['error']}")
    counts = index["counts"]
    handled = counts["indexed"] + counts["failed"] + counts["skipped"]
    if counts["pending"] == 0 and counts["indexing"] == 0 and handled == total:
        return index
    return None


def ask(client: httpx.Client, chat_id: int, question: str) -> list[dict]:
    events = []
    with client.stream(
        "POST", f"/api/chats/{chat_id}/messages", json={"content": question}, timeout=300
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:") :]))
    return events


def check_answer(question: dict, events: list[dict]) -> dict:
    sources = next((e["sources"] for e in events if e["type"] == "sources"), [])
    text = "".join(e["text"] for e in events if e["type"] == "token")
    errors = [e["message"] for e in events if e["type"] == "error"]
    problems = []
    if errors:
        problems.append(f"error events: {errors}")
    if not events or events[-1]["type"] != "done":
        problems.append("the stream did not end with a done event")
    if question["file"] is None:
        if sources:
            problems.append("an off-topic question got sources")
        if text != NOT_FOUND[question["lang"]]:
            problems.append("an off-topic question did not get the not-found reply")
    else:
        if question["file"] not in {s["file"] for s in sources}:
            problems.append(f"{question['file']} is not among the sources")
        if not text.strip():
            problems.append("empty answer")
    cited = any(1 <= int(n) <= len(sources) for n in CITATION.findall(text))
    return {
        "id": question["id"],
        "answer": text[:400],
        "sources": [s["file"] for s in sources],
        "cited": cited,
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end check of the packaged Tamra.exe")
    parser.add_argument("--exe", type=Path, default=ROOT / "dist" / "Tamra" / "Tamra.exe")
    parser.add_argument("--out", type=Path, help="also write the report as JSON")
    args = parser.parse_args()
    if not args.exe.is_file():
        raise SystemExit(f"missing {args.exe}; build it with ./scripts/build.ps1")
    if port_in_use(PORT):
        raise SystemExit(f"port {PORT} is in use; stop the other Tamra dev server first")
    questions = {q["id"]: q for q in load_questions()}
    report: dict = {"exe": str(args.exe), "answers": []}
    problems: list[str] = []
    before = llama_server_pids()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        docs = Path(tmp) / "docs"
        build(docs)
        total = len(list(docs.iterdir()))
        env = {
            **os.environ,
            "TAMRA_DATA_DIR": str(Path(tmp) / "data"),
            "TAMRA_MODELS_DIR": str(ROOT / ".models"),
        }
        proc = subprocess.Popen([str(args.exe), "--dev"], env=env)
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{PORT}",
                headers={"X-Tamra-Token": "dev"},
                trust_env=False,
                timeout=30,
            ) as client:
                started = time.monotonic()
                wait_for(lambda: healthy(client), 60, "the API")
                report["startup_s"] = round(time.monotonic() - started, 1)
                client.put(
                    "/api/collection", json={"name": "Smoke", "folder_path": str(docs)}
                ).raise_for_status()
                started = time.monotonic()
                index = wait_for(lambda: settled(client, total), 900, "indexing")
                report["index_s"] = round(time.monotonic() - started, 1)
                report["index_counts"] = index["counts"]
                if index["counts"]["indexed"] != total:
                    problems.append(f"not every file was indexed: {index['problems']}")
                for question_id in SMOKE_IDS:
                    question = questions[question_id]
                    chat_id = client.post("/api/chats").json()["id"]
                    started = time.monotonic()
                    result = check_answer(question, ask(client, chat_id, question["question"]))
                    result["seconds"] = round(time.monotonic() - started, 1)
                    saved = client.get(f"/api/chats/{chat_id}").json()["messages"]
                    answers = [m for m in saved if m["role"] == "assistant"]
                    if not answers or (question["file"] and not answers[-1]["sources"]):
                        result["problems"].append("the answer or its sources were not saved")
                    report["answers"].append(result)
                (docs / "watcher-check.md").write_text(
                    "The rooftop garden opens at 6 pm on Fridays.", encoding="utf-8"
                )
                started = time.monotonic()
                wait_for(lambda: settled(client, total + 1), 120, "the folder watcher")
                report["watcher_s"] = round(time.monotonic() - started, 1)
        finally:
            proc.kill()  # a hard kill: llama-server must still end with Tamra (Job Object)
            proc.wait(timeout=30)
        time.sleep(2)
    left = sorted(llama_server_pids() - before)
    if left:
        problems.append(f"llama-server still running after Tamra ended: {left}")
    for answer in report["answers"]:
        problems += [f"{answer['id']}: {p}" for p in answer["problems"]]
    answerable = [a for a in report["answers"] if questions[a["id"]]["file"]]
    report["cited_answers"] = f"{sum(a['cited'] for a in answerable)} of {len(answerable)}"
    if not any(a["cited"] for a in answerable):
        problems.append("no answer contained a [n] citation")
    report["problems"] = problems
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))  # ASCII-escaped, so any console can print it
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
