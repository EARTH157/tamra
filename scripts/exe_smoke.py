"""End-to-end check of the packaged app through its API (the M1 exit criterion).

    uv run python scripts/exe_smoke.py [--exe dist/Tamra/Tamra.exe] [--out report.json]

Starts Tamra.exe in dev mode (API only on 127.0.0.1:8765, token "dev") with a scratch data
folder and a scratch models folder that holds only the embedding model (hard-linked from
.models), then:

1. Imports the dev GGUF that fetch_assets.py downloads (qwen2.5-0.5b-instruct) through
   POST /api/models/import, as an uncatalogued model, and selects it as the local model.
2. Indexes the eval corpus, asks one question in each language and one off-topic question, and
   checks the streamed answers and the saved chats (provider "local").
3. Asks one question with "think" on; the 0.5B model cannot think, so this only checks that the
   request is accepted.
4. Adds a file to check the folder watcher.
5. API mode: starts a second llama-server with the same model, a free port and a throwaway API
   key, points the "openai" provider at it, saves the key through the API (the real Windows
   Credential Manager, service "Tamra"), asks one question (provider must be "openai"), switches
   back to local and asks again. The key is deleted at the end and the deletion is verified. The
   step is skipped when a key for "openai" is already stored: that one is never touched.
6. Ends the exe by PID and checks that its llama-server ended with it.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from build_eval_corpus import build
from eval_retrieval import load_questions

from tamra import secrets as key_store
from tamra.answer import NOT_FOUND
from tamra.llm.llama_server import LlamaServer

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


def ask(client: httpx.Client, chat_id: int, question: str, think: bool = False) -> list[dict]:
    events = []
    with client.stream(
        "POST",
        f"/api/chats/{chat_id}/messages",
        json={"content": question, "think": think},
        timeout=300,
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


def link_embedding_model(src: Path, dest: Path) -> None:
    """Make src/bge-m3 available under dest without copying (a copy when hard links fail)."""
    for f in (src / "bge-m3").iterdir():
        target = dest / "bge-m3" / f.name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(f, target)
        except OSError:
            shutil.copy2(f, target)


def put_settings(client: httpx.Client, **fields) -> dict:
    response = client.put("/api/settings", json=fields)
    if response.status_code != 200:
        raise SystemExit(f"PUT /api/settings {sorted(fields)} gave {response.status_code}")
    return response.json()


def last_answer(client: httpx.Client, chat_id: int) -> dict | None:
    saved = client.get(f"/api/chats/{chat_id}").json()["messages"]
    answers = [m for m in saved if m["role"] == "assistant"]
    return answers[-1] if answers else None


def import_dev_model(client: httpx.Client, gguf: Path, report: dict, problems: list[str]) -> None:
    """Import the dev GGUF as an uncatalogued model and select it as the local model."""
    before = client.get("/api/models").json()
    if any(m["installed"] for m in before["local"]) or before["uncatalogued"]:
        problems.append("the scratch models folder was expected to hold no local model")
    imported = client.post("/api/models/import", json={"path": str(gguf)}, timeout=300)
    if imported.status_code != 200:
        raise SystemExit(f"model import failed: {imported.status_code} {imported.text[:200]}")
    result = imported.json()
    if result["catalogued"] or not result["warning"] or not result["id"].startswith("import:"):
        problems.append(f"unexpected import result: {result}")
    models = client.get("/api/models").json()
    if result["id"] not in {m["id"] for m in models["uncatalogued"]}:
        problems.append("the imported model is not listed under uncatalogued")
    put_settings(client, local_model_id=result["id"])
    active = client.get("/api/models").json()["active"]
    if active["id"] != result["id"] or active["mode"] != "local":
        problems.append(f"the imported model is not the active local model: {active}")
    report["import"] = {"id": result["id"], "catalogued": result["catalogued"]}


def check_thinking_accepted(
    client: httpx.Client, question: dict, report: dict, problems: list[str]
) -> None:
    """The 0.5B model cannot think: the request must still be accepted and finish cleanly."""
    chat_id = client.post("/api/chats").json()["id"]
    events = ask(client, chat_id, question["question"], think=True)
    errors = [e["message"] for e in events if e["type"] == "error"]
    ok = not errors and bool(events) and events[-1]["type"] == "done"
    report["think_accepted"] = ok
    if not ok:
        problems.append(f"a think=true request was not accepted: {errors}")


def api_mode_step(
    client: httpx.Client,
    llama_exe: Path,
    gguf: Path,
    log_dir: Path,
    question: dict,
    report: dict,
    problems: list[str],
) -> None:
    """Switch to an OpenAI-compatible server (a second llama-server), then back to local.

    The throwaway key goes into the real Credential Manager entry ("Tamra", "openai"). A key
    that is already there is never overwritten or deleted: the step is skipped instead.
    """
    api: dict = {"skipped": None}
    report["api_mode"] = api
    try:
        if key_store.get_api_key("openai"):
            api["skipped"] = "a key for 'openai' is already stored; it was not touched"
            return
    except Exception as e:  # no usable credential store: the step cannot run safely
        api["skipped"] = f"the credential store could not be read ({type(e).__name__})"
        return
    before = client.get("/api/settings").json()
    saved = {k: before[k] for k in ("mode", "api_provider", "api_model", "api_base_url")}
    put_settings(client, api_provider="openai")
    if client.get("/api/settings").json()["api_key_set"]:  # the exe's own view agrees
        api["skipped"] = "the app reports a key for 'openai'; it was not touched"
        put_settings(client, **saved)
        return
    throwaway = secrets.token_hex(16)  # never printed or reported
    key_written = False
    try:
        log_file = log_dir / "api-llama-server.log"
        with LlamaServer(llama_exe, gguf, log_file, gpu=False, api_key=throwaway) as srv:
            put_settings(client, api_base_url=srv.base_url, api_model="local")
            key_written = True  # set before the call: a half-failed write is still cleaned up
            client.put(
                "/api/settings/api-key", json={"provider": "openai", "key": throwaway}
            ).raise_for_status()
            now = client.get("/api/settings").json()
            api["key_set_through_api"] = now["api_key_set"]
            if not now["api_key_set"]:
                problems.append("the API key was not reported as set after saving it")
            test = client.post("/api/settings/test-connection", timeout=60).json()
            api["test_connection"] = test["ok"]
            if not test["ok"]:
                problems.append(f"test-connection failed: {test['reason']}")
            put_settings(client, mode="api")
            chat_id = client.post("/api/chats").json()["id"]
            started = time.monotonic()
            result = check_answer(question, ask(client, chat_id, question["question"]))
            result["seconds"] = round(time.monotonic() - started, 1)
            message = last_answer(client, chat_id)
            api["provider"] = message["provider"] if message else None
            if api["provider"] != "openai":
                result["problems"].append(f"expected provider 'openai', got {api['provider']!r}")
            api["answer"] = result
            put_settings(client, mode="local")
            chat_id = client.post("/api/chats").json()["id"]
            started = time.monotonic()
            back = check_answer(question, ask(client, chat_id, question["question"]))
            back["seconds"] = round(time.monotonic() - started, 1)
            message = last_answer(client, chat_id)
            api["back_to_local_provider"] = message["provider"] if message else None
            if api["back_to_local_provider"] != "local":
                back["problems"].append(
                    f"expected provider 'local', got {api['back_to_local_provider']!r}"
                )
            api["back_to_local"] = back
            problems += [f"api mode {question['id']}: {p}" for p in result["problems"]]
            problems += [f"back to local {question['id']}: {p}" for p in back["problems"]]
    finally:
        if key_written:
            try:  # a failure here must never replace the error that is already propagating
                try:
                    client.put("/api/settings/api-key", json={"provider": "openai", "key": ""})
                except httpx.HTTPError:
                    pass
                if key_store.get_api_key("openai"):  # the API could not delete it: do it directly
                    key_store.delete_api_key("openai")
                gone = key_store.get_api_key("openai") is None
                if not gone:
                    problems.append("the throwaway API key could not be deleted")
            except Exception as e:  # the key store itself failed (never print its message)
                gone = False
                problems.append(f"the throwaway API key cleanup failed ({type(e).__name__})")
            api["key_deleted"] = gone
        try:
            # The provider is still "openai" here, so api_key_set is about the throwaway key.
            if key_written and client.get("/api/settings").json()["api_key_set"]:
                problems.append("the app still reports an API key after deleting it")
            put_settings(client, **saved)
        except (httpx.HTTPError, SystemExit):
            problems.append("the changed settings could not be restored")


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end check of the packaged Tamra.exe")
    parser.add_argument("--exe", type=Path, default=ROOT / "dist" / "Tamra" / "Tamra.exe")
    parser.add_argument("--out", type=Path, help="also write the report as JSON")
    args = parser.parse_args()
    if not args.exe.is_file():
        raise SystemExit(f"missing {args.exe}; build it with ./scripts/build.ps1")
    if port_in_use(PORT):
        raise SystemExit(f"port {PORT} is in use; stop the other Tamra dev server first")
    ggufs = sorted((ROOT / ".models").glob("qwen2.5-0.5b-instruct*.gguf"))
    if not ggufs:
        raise SystemExit("missing the dev model in .models; run scripts/fetch_assets.py")
    dev_gguf = ggufs[0]
    questions = {q["id"]: q for q in load_questions()}
    report: dict = {"exe": str(args.exe), "answers": []}
    problems: list[str] = []
    before = llama_server_pids()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        docs = Path(tmp) / "docs"
        build(docs)
        total = len(list(docs.iterdir()))
        models = Path(tmp) / "models"  # only the embedding model: the LLM comes from the import
        link_embedding_model(ROOT / ".models", models)
        env = {
            **os.environ,
            "TAMRA_DATA_DIR": str(Path(tmp) / "data"),
            "TAMRA_MODELS_DIR": str(models),
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
                import_dev_model(client, dev_gguf, report, problems)
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
                    if question["file"] and answers and answers[-1]["provider"] != "local":
                        result["problems"].append("the answer was not recorded as provider local")
                    report["answers"].append(result)
                check_thinking_accepted(client, questions[SMOKE_IDS[1]], report, problems)
                (docs / "watcher-check.md").write_text(
                    "The rooftop garden opens at 6 pm on Fridays.", encoding="utf-8"
                )
                started = time.monotonic()
                wait_for(lambda: settled(client, total + 1), 120, "the folder watcher")
                report["watcher_s"] = round(time.monotonic() - started, 1)
                api_mode_step(
                    client,
                    ROOT / "vendor" / "llama" / "llama-server.exe",
                    dev_gguf,
                    Path(tmp),
                    questions[SMOKE_IDS[1]],
                    report,
                    problems,
                )
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
