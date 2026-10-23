"""End-to-end check of the packaged app through its API (the M1 exit criterion).

    uv run python scripts/exe_smoke.py [--exe dist/Tamra/Tamra.exe] [--out report.json]

Starts Tamra.exe in dev mode (API only on 127.0.0.1:8765; the packaged exe takes the token
from TAMRA_DEV_TOKEN, and the smoke makes a fresh one that it never prints) with a scratch data
folder and a scratch models folder that holds only the embedding and OCR models (hard-linked
from .models), then:

1. Imports the dev GGUF that fetch_assets.py downloads (qwen2.5-0.5b-instruct) through
   POST /api/models/import, as an uncatalogued model, and selects it as the local model.
2. Creates two projects, "Company" and "Contracts", from two sibling folders that hold six
   documents of the eval corpus, and waits for both to be indexed. Asks one question in each
   language and one off-topic question in chats that search both projects, and checks the
   streamed answers and the saved chats (provider "local").
3. Asks one question with "think" on; the 0.5B model cannot think, so this only checks that the
   request is accepted.
4. Adds a file to the Company folder to check the folder watcher.
5. Checks a source: attribution of a sentence of the lease answer (at least one match), locating
   the first match in its PDF (found, with highlight boxes), the page route (PNG bytes), and the
   text route for a cited text file. Timings are recorded: the first attribution call, a repeat
   (cached), and a page render at scale 1.5.
6. API mode: starts a second llama-server with the same model, a free port and a throwaway API
   key, points the "openai" provider at it, saves the key through the API (the real Windows
   Credential Manager, service "Tamra"), asks one question (provider must be "openai"), switches
   back to local and asks again. The key is deleted at the end and the deletion is verified. The
   step is skipped when a key for "openai" is already stored: that one is never touched.
7. Projects and search scope (M4), through the API: a Company chat asks the lease question and
   gets no source from Contracts; after PUT scope to both projects the same question cites
   apartment-lease.pdf from Contracts; moving the chat to Contracts lists it there in
   GET /api/chats; a rename (and a clashing rename, 409) and a rebuild of one project work; and
   deleting Contracts reports the chats it deleted and leaves its folder alone.
8. A scanned page (M5): a "Scans" project over an image-only PDF rendered at run time from the
   committed tests/fixtures/ocr/th_0.pdf. It must be indexed through OCR, a Thai question about
   it must cite it with an " · OCR" label, and locating that source must find an OCR page with
   highlight boxes.
9. AI apps (MCP): turns AI access on, launches `Tamra.exe mcp` with the scratch data folder as a
   stdio MCP server, checks that it offers list_projects and search, that list_projects names the
   same projects as GET /api/projects, and that the Thai leave question finds hr-leave-policy.docx;
   then turns access off and checks that the next search is refused with "AI access is off".
10. Ends the exe by PID and checks that its llama-server ended with it.
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

import anyio
import httpx
from build_eval_corpus import build  # also puts tests/ on sys.path for docgen
from docgen import image_pdf
from eval_retrieval import load_questions
from mcp import Client, StdioServerParameters

from tamra import secrets as key_store
from tamra.answer import NOT_FOUND
from tamra.llm.llama_server import LlamaServer

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765
PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
PAGE_SCALE = 1.5  # the viewer's default render scale
SMOKE_IDS = ("th-leave-annual", "en-lease-rent", "zh-warranty-sofa", "none-world-cup")
CITATION = re.compile(r"\[(\d+)\]")
SCAN_SOURCE = ROOT / "tests" / "fixtures" / "ocr" / "th_0.pdf"  # a committed text PDF
SCAN_QUESTION = "ผู้ใช้ห้องปฏิบัติการต้องผ่านการอบรมอะไรก่อนเริ่มใช้งานเครื่องมือ"
PROJECT_FILES = {  # project name -> eval corpus documents (rendered names)
    "Company": ("company-history.txt", "hr-leave-policy.docx", "it-security-policy.md"),
    "Contracts": ("apartment-lease.pdf", "shipping-policy.pdf", "furniture-warranty.txt"),
}


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


def settled(client: httpx.Client, totals: dict[int, int]) -> dict[int, dict] | None:
    """The index status of each project (id -> status) once all its files have been handled."""
    indexes = {p["id"]: p["index"] for p in client.get("/api/projects").json()["projects"]}
    if not set(totals) <= set(indexes):
        return None
    for project_id in totals:
        if indexes[project_id]["error"]:
            raise SystemExit(f"indexing stopped: {indexes[project_id]['error']}")
    for project_id, total in totals.items():
        counts = indexes[project_id]["counts"]
        handled = counts["indexed"] + counts["failed"] + counts["skipped"]
        if counts["pending"] or counts["indexing"] or handled != total:
            return None
    return {project_id: indexes[project_id] for project_id in totals}


def new_chat(client: httpx.Client, scope: list[int]) -> int:
    """A chat whose home is the first project and which searches exactly the projects in scope."""
    chat = client.post("/api/chats", json={"project_id": scope[0]})
    chat.raise_for_status()
    chat_id = chat.json()["id"]
    if sorted(scope) != chat.json()["scope"]:
        client.put(f"/api/chats/{chat_id}/scope", json={"project_ids": scope}).raise_for_status()
    return chat_id


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


def sentence_to_check(answer: str, sources: list[dict]) -> str:
    """A sentence of the answer that cites a PDF source (else the first), markers removed."""
    pieces: list[str] = []
    for piece in (p.strip() for p in re.split(r"(?<=[.!?])\s+", answer)):
        if pieces and not CITATION.sub("", piece).strip():
            pieces[-1] += " " + piece  # "...month. [1]": the marker belongs to the sentence before
        elif piece:
            pieces.append(piece)
    pdf_numbers = {s["n"] for s in sources if s["file"].lower().endswith(".pdf")}
    for piece in pieces:
        cited = {int(n) for n in CITATION.findall(piece)}
        if cited & pdf_numbers:
            return _unmarked(piece)
    return _unmarked(pieces[0] if pieces else answer)


def _unmarked(text: str) -> str:
    """The text without citation markers or the space before them."""
    return re.sub(r"\s*(?:" + CITATION.pattern + ")", "", text).strip()


def timed(call) -> tuple[httpx.Response, float]:
    started = time.monotonic()
    response = call()
    return response, round(time.monotonic() - started, 3)


def check_source_viewer(
    client: httpx.Client,
    lease: dict | None,
    text_answer: dict | None,
    report: dict,
    problems: list[str],
) -> None:
    """Attribution, locate, the page image and the text view, on answers that were just saved."""
    viewer: dict = {}
    report["viewer"] = viewer
    if lease is None or not lease["sources"]:
        problems.append("viewer: the lease answer was not saved with sources")
        return
    sentence = sentence_to_check(lease["content"], lease["sources"])
    viewer["sentence"] = sentence[:200]
    first, first_s = timed(
        lambda: client.post(
            "/api/attribution", json={"message_id": lease["id"], "selection": sentence}, timeout=120
        )
    )
    if first.status_code != 200:
        problems.append(f"viewer: attribution gave {first.status_code}")
        return
    again, cached_s = timed(
        lambda: client.post(
            "/api/attribution", json={"message_id": lease["id"], "selection": sentence}, timeout=120
        )
    )
    viewer["attribution_first_s"] = first_s
    viewer["attribution_repeat_s"] = cached_s
    matches = first.json()["matches"]
    viewer["matches"] = [{k: m[k] for k in ("n", "label", "file", "changed")} for m in matches]
    if not matches:
        problems.append("viewer: attribution found no match for a sentence of the answer")
    elif again.json()["matches"] != matches:
        problems.append("viewer: a repeated attribution gave a different result")
    pdf_match = next((m for m in matches if m["file"].lower().endswith(".pdf")), None)
    if matches and pdf_match is None:
        problems.append("viewer: no match is in a PDF source")
    if pdf_match is not None:
        located = client.get(
            f"/api/sources/{lease['id']}/{pdf_match['n']}/locate",
            params={"start": pdf_match["start"], "end": pdf_match["end"]},
        )
        if located.status_code != 200:
            problems.append(f"viewer: locate gave {located.status_code}")
        else:
            where = located.json()
            viewer["locate"] = {
                k: where.get(k) for k in ("kind", "found", "page", "page_count", "changed")
            }
            viewer["locate"]["rects"] = len(where.get("rects") or [])
            if not where.get("found") or where.get("kind") != "pdf":
                problems.append(f"viewer: the PDF match was not found in its file: {where}")
            elif not where.get("rects"):
                problems.append("viewer: locating a PDF passage returned no highlight boxes")
            else:
                page_url = f"/api/files/{where['file_id']}/pages/{where['page']}"
                client.get(page_url, params={"scale": PAGE_SCALE})  # warm pdfium and the file
                page, page_s = timed(lambda: client.get(page_url, params={"scale": PAGE_SCALE}))
                viewer["page_png"] = {
                    "status": page.status_code,
                    "bytes": len(page.content),
                    "render_s": page_s,
                    "scale": PAGE_SCALE,
                }
                if page.status_code != 200 or not page.content.startswith(PNG_SIGNATURE):
                    problems.append("viewer: the page route did not return a PNG")
    text_source = next(
        (
            s
            for s in (text_answer or {}).get("sources", [])
            if s["file_id"] is not None and s["file"].lower().endswith((".txt", ".md"))
        ),
        None,
    )
    if text_source is None:
        problems.append("viewer: no cited text source to check the text route with")
        return
    shown = client.get(f"/api/files/{text_source['file_id']}/text")
    body = shown.json() if shown.status_code == 200 else {}
    viewer["text"] = {
        "file": text_source["file"],
        "status": shown.status_code,
        "kind": body.get("kind"),
        "lines": len(body.get("lines", [])),
    }
    if body.get("kind") != "text" or not any(line.strip() for line in body.get("lines", [])):
        problems.append("viewer: the text route returned no lines for a cited text file")


def link_models(src: Path, dest: Path) -> None:
    """Make the embedding and OCR model folders of src available under dest without copying
    (a copy when hard links fail). The LLM is not linked: it comes from the import step."""
    for folder in ("bge-m3", "ppocr-v5"):
        if not (src / folder).is_dir():
            raise SystemExit(f"missing {src / folder}; run scripts/fetch_assets.py")
        for f in (src / folder).iterdir():
            target = dest / folder / f.name
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
    client: httpx.Client, scope: list[int], question: dict, report: dict, problems: list[str]
) -> None:
    """The 0.5B model cannot think: the request must still be accepted and finish cleanly."""
    chat_id = new_chat(client, scope)
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
    scope: list[int],
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
            chat_id = new_chat(client, scope)
            started = time.monotonic()
            result = check_answer(question, ask(client, chat_id, question["question"]))
            result["seconds"] = round(time.monotonic() - started, 1)
            message = last_answer(client, chat_id)
            api["provider"] = message["provider"] if message else None
            if api["provider"] != "openai":
                result["problems"].append(f"expected provider 'openai', got {api['provider']!r}")
            api["answer"] = result
            put_settings(client, mode="local")
            chat_id = new_chat(client, scope)
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


def question_sources(client: httpx.Client, chat_id: int, question: dict) -> tuple[list[dict], str]:
    """Ask in a chat; the streamed sources and the answer text. Problems end the run loudly."""
    events = ask(client, chat_id, question["question"])
    errors = [e["message"] for e in events if e["type"] == "error"]
    if errors or not events or events[-1]["type"] != "done":
        raise SystemExit(f"project step: the answer failed: {errors or 'no done event'}")
    sources = next((e["sources"] for e in events if e["type"] == "sources"), [])
    return sources, "".join(e["text"] for e in events if e["type"] == "token")


def check_projects(
    client: httpx.Client,
    ids: dict[str, int],
    folders: dict[str, Path],
    question: dict,
    report: dict,
    problems: list[str],
) -> None:
    """Search scope, moving a chat, renaming, rebuilding and deleting a project (M4)."""
    steps: dict = {}
    report["projects"] = steps
    company, contracts = ids["Company"], ids["Contracts"]
    both = sorted(ids.values())

    # A chat that searches Company only must not see the lease in Contracts.
    chat_id = new_chat(client, [company])
    sources, text = question_sources(client, chat_id, question)
    steps["company_only"] = {
        "sources": [f"{s['project']}: {s['file']}" for s in sources],
        "not_found": text == NOT_FOUND[question["lang"]],
        "answer": text[:200],
    }
    if any(s["project"] == "Contracts" or s["file"] == question["file"] for s in sources):
        problems.append("projects: a Company-only chat got a source from Contracts")

    # Widening the scope to both projects finds it.
    scope = client.put(f"/api/chats/{chat_id}/scope", json={"project_ids": both})
    steps["scope_put"] = {"status": scope.status_code, "scope": scope.json().get("scope")}
    if scope.status_code != 200 or scope.json()["scope"] != both:
        problems.append(f"projects: PUT scope gave {scope.status_code} {scope.text[:200]}")
    sources, text = question_sources(client, chat_id, question)
    steps["both"] = {"sources": [f"{s['project']}: {s['file']}" for s in sources]}
    if not any(s["project"] == "Contracts" and s["file"] == question["file"] for s in sources):
        problems.append("projects: the widened scope did not cite apartment-lease.pdf (Contracts)")

    # Moving the chat to Contracts lists it there; its scope is unchanged.
    moved = client.patch(f"/api/chats/{chat_id}", json={"project_id": contracts})
    listed = {c["id"]: c for c in client.get("/api/chats").json()["chats"]}
    steps["move"] = {
        "status": moved.status_code,
        "project_id": listed.get(chat_id, {}).get("project_id"),
        "scope": listed.get(chat_id, {}).get("scope"),
    }
    if moved.status_code != 200 or listed.get(chat_id, {}).get("project_id") != contracts:
        problems.append(f"projects: the chat is not listed under Contracts: {steps['move']}")
    if listed.get(chat_id, {}).get("scope") != both:
        problems.append("projects: moving the chat changed its search scope")
    homes = [c["project_id"] for c in listed.values()]

    # Rename; a name that clashes (any case) is refused.
    renamed = client.patch(f"/api/projects/{company}", json={"name": "Company records"})
    clash = client.patch(f"/api/projects/{contracts}", json={"name": "company RECORDS"})
    names = {p["id"]: p["name"] for p in client.get("/api/projects").json()["projects"]}
    steps["rename"] = {
        "status": renamed.status_code,
        "clash_status": clash.status_code,
        "names": names,
    }
    if renamed.status_code != 200 or names.get(company) != "Company records":
        problems.append(f"projects: rename failed: {steps['rename']}")
    if clash.status_code != 409 or names.get(contracts) != "Contracts":
        problems.append(f"projects: a clashing name was not refused with 409: {steps['rename']}")

    # Rebuild Contracts only: the other project is left alone, the lease is found again.
    before = {p["id"]: p["index"]["counts"] for p in client.get("/api/projects").json()["projects"]}
    rebuild = client.post(f"/api/projects/{contracts}/rebuild")
    seen_unindexed = False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not seen_unindexed:  # the reset is quick: poll fast
        counts = next(
            p["index"]["counts"]
            for p in client.get("/api/projects").json()["projects"]
            if p["id"] == contracts
        )
        seen_unindexed = counts["indexed"] < before[contracts]["indexed"]
        time.sleep(0.05)
    wait_for(lambda: settled(client, {company: 4, contracts: 3}), 300, "the rebuild")
    after = {p["id"]: p["index"]["counts"] for p in client.get("/api/projects").json()["projects"]}
    sources, _ = question_sources(client, chat_id, question)
    steps["rebuild"] = {
        "status": rebuild.status_code,
        "saw_files_requeued": seen_unindexed,
        "counts_after": after[contracts],
        "company_counts_unchanged": after[company] == before[company],
        "sources": [f"{s['project']}: {s['file']}" for s in sources],
    }
    if rebuild.status_code != 202:
        problems.append(f"projects: rebuild gave {rebuild.status_code}")
    if after[company] != before[company] or after[contracts] != before[contracts]:
        problems.append(f"projects: the counts changed across a rebuild: {before} -> {after}")
    if not any(s["project"] == "Contracts" and s["file"] == question["file"] for s in sources):
        problems.append("projects: the lease was not found after rebuilding Contracts")

    # Delete Contracts: its chats go with it, its folder stays.
    expected = homes.count(contracts)
    deleted = client.delete(f"/api/projects/{contracts}")
    remaining = client.get("/api/projects").json()["projects"]
    chats_left = client.get("/api/chats").json()["chats"]
    steps["delete"] = {
        "status": deleted.status_code,
        "chats_deleted": deleted.json().get("chats_deleted"),
        "expected_chats_deleted": expected,
        "projects_left": [p["name"] for p in remaining],
        "scopes_left": [c["scope"] for c in chats_left],
        "folder_files_left": len(list(folders["Contracts"].iterdir())),
    }
    if deleted.status_code != 200 or deleted.json().get("chats_deleted") != expected:
        problems.append(f"projects: deleting Contracts gave {steps['delete']}")
    if [p["id"] for p in remaining] != [company]:
        problems.append("projects: Contracts is still listed after deleting it")
    if any(contracts in c["scope"] or not c["scope"] for c in chats_left) or any(
        c["id"] == chat_id for c in chats_left
    ):
        problems.append("projects: a chat still searches Contracts, or the moved chat survived")
    if steps["delete"]["folder_files_left"] != len(PROJECT_FILES["Contracts"]):
        problems.append("projects: deleting the project changed the user's folder")


def check_scanned(client: httpx.Client, folder: Path, report: dict, problems: list[str]) -> None:
    """M5: a scanned Thai page (an image-only PDF rendered from a committed text PDF) is read by
    OCR, found by a question, and its cited source locates to an OCR page with boxes."""
    step: dict = {}
    report["scanned"] = step
    step["ocr_installed"] = client.get("/api/models").json()["ocr"]["installed"]
    folder.mkdir()
    image_pdf(SCAN_SOURCE, folder / "lab-rules-scan.pdf", dpi=200)
    created = client.post("/api/projects", json={"name": "Scans", "folder_path": str(folder)})
    created.raise_for_status()
    project_id = created.json()["id"]
    started = time.monotonic()
    index = wait_for(lambda: settled(client, {project_id: 1}), 300, "OCR indexing")[project_id]
    step["index_s"] = round(time.monotonic() - started, 1)
    step["counts"] = index["counts"]
    if index["counts"]["indexed"] != 1 or index["problems"]:
        problems.append(f"scanned: the scan was not indexed cleanly: {index['problems']}")
        return
    chat_id = new_chat(client, [project_id])
    ask(client, chat_id, SCAN_QUESTION)
    answer = last_answer(client, chat_id)
    sources = (answer or {}).get("sources", [])
    step["labels"] = [s["label"] for s in sources]
    step["cited"] = bool(answer and CITATION.search(answer["content"]))
    scanned = [s for s in sources if s["location"].get("ocr")]
    if not scanned:
        problems.append("scanned: the answer has no source on an OCR page")
        return
    if not scanned[0]["label"].endswith("· OCR"):
        problems.append(f"scanned: the source label does not say OCR: {scanned[0]['label']}")
    located = client.get(f"/api/sources/{answer['id']}/{scanned[0]['n']}/locate")
    where = located.json() if located.status_code == 200 else {}
    step["locate"] = {k: where.get(k) for k in ("found", "kind", "page", "ocr")}
    step["locate"]["rects"] = len(where.get("rects") or [])
    if not (where.get("found") and where.get("ocr") and where.get("rects")):
        problems.append(f"scanned: the OCR source did not locate with boxes: {step['locate']}")


async def _mcp_session(exe: Path, data_dir: Path, query: str, turn_off) -> dict:
    """One stdio session with `Tamra.exe mcp`: the tools, both calls, then a search after
    turn_off() switched AI access off."""
    server = StdioServerParameters(
        command=str(exe), args=["mcp"], env={"TAMRA_DATA_DIR": str(data_dir)}
    )
    async with Client(server) as mcp:
        tools = sorted(tool.name for tool in (await mcp.list_tools()).tools)
        projects = await mcp.call_tool("list_projects", {})
        found = await mcp.call_tool("search", {"query": query, "k": 3})
        turn_off()
        refused = await mcp.call_tool("search", {"query": query})
    return {"tools": tools, "projects": projects, "found": found, "refused": refused}


def check_mcp(
    client: httpx.Client,
    exe: Path,
    data_dir: Path,
    question: dict,
    report: dict,
    problems: list[str],
) -> None:
    """MCP (docs/superpowers/specs/2026-10-22-tamra-mcp-design.md): the packaged exe as a stdio
    MCP server, with AI access on and then off."""
    put_settings(client, mcp_enabled=True)
    started = time.monotonic()
    calls = anyio.run(
        _mcp_session,
        exe,
        data_dir,
        question["question"],
        lambda: put_settings(client, mcp_enabled=False),
    )
    report["mcp_s"] = round(time.monotonic() - started, 1)
    if calls["tools"] != ["list_projects", "search"]:
        problems.append(f"mcp: unexpected tools {calls['tools']}")
    listed = sorted(
        p["name"] for p in (calls["projects"].structured_content or {}).get("projects", [])
    )
    expected = sorted(p["name"] for p in client.get("/api/projects").json()["projects"])
    if listed != expected:
        problems.append(f"mcp: list_projects gave {listed}, the app has {expected}")
    passages = (calls["found"].structured_content or {}).get("passages", [])
    report["mcp_files"] = [p["file"] for p in passages]
    if calls["found"].is_error or not any(p["file"] == question["file"] for p in passages):
        problems.append(f"mcp: search did not find {question['file']}")
    refused = calls["refused"]
    text = refused.content[0].text if refused.content else ""
    if not refused.is_error or "AI access is off" not in text:
        problems.append(f"mcp: a search with access off was not refused: {text!r}")


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
        corpus = Path(tmp) / "corpus"
        build(corpus)
        folders = {name: Path(tmp) / name for name in PROJECT_FILES}  # siblings of the data dir
        for name, files in PROJECT_FILES.items():
            folders[name].mkdir()
            for file in files:
                shutil.copy2(corpus / file, folders[name] / file)
        shutil.rmtree(corpus)
        models = Path(tmp) / "models"  # embedding + OCR models: the LLM comes from the import
        link_models(ROOT / ".models", models)
        token = secrets.token_urlsafe(32)  # never printed or reported
        env = {
            **os.environ,
            "TAMRA_DATA_DIR": str(Path(tmp) / "data"),
            "TAMRA_MODELS_DIR": str(models),
            "TAMRA_DEV_TOKEN": token,
        }
        proc = subprocess.Popen([str(args.exe), "--dev"], env=env)
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{PORT}",
                headers={"X-Tamra-Token": token},
                trust_env=False,
                timeout=30,
            ) as client:
                started = time.monotonic()
                wait_for(lambda: healthy(client), 60, "the API")
                report["startup_s"] = round(time.monotonic() - started, 1)
                import_dev_model(client, dev_gguf, report, problems)
                ids: dict[str, int] = {}
                for name, folder in folders.items():
                    created = client.post(
                        "/api/projects", json={"name": name, "folder_path": str(folder)}
                    )
                    created.raise_for_status()
                    ids[name] = created.json()["id"]
                totals = {ids[name]: len(files) for name, files in PROJECT_FILES.items()}
                scope = sorted(ids.values())  # the M1-M3 questions search both projects
                started = time.monotonic()
                indexes = wait_for(lambda: settled(client, totals), 900, "indexing")
                report["index_s"] = round(time.monotonic() - started, 1)
                report["index_counts"] = {n: indexes[i]["counts"] for n, i in ids.items()}
                for name, project_id in ids.items():
                    if indexes[project_id]["counts"]["indexed"] != totals[project_id]:
                        problems.append(
                            f"{name}: not every file was indexed: {indexes[project_id]['problems']}"
                        )
                saved_answers: dict[str, dict] = {}
                for question_id in SMOKE_IDS:
                    question = questions[question_id]
                    chat_id = new_chat(client, scope)
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
                    if answers:
                        saved_answers[question_id] = answers[-1]
                check_source_viewer(
                    client,
                    saved_answers.get("en-lease-rent"),
                    saved_answers.get("zh-warranty-sofa"),
                    report,
                    problems,
                )
                check_thinking_accepted(client, scope, questions[SMOKE_IDS[1]], report, problems)
                (folders["Company"] / "watcher-check.md").write_text(
                    "The rooftop garden opens at 6 pm on Fridays.", encoding="utf-8"
                )
                totals[ids["Company"]] += 1
                started = time.monotonic()
                wait_for(lambda: settled(client, totals), 120, "the folder watcher")
                report["watcher_s"] = round(time.monotonic() - started, 1)
                api_mode_step(
                    client,
                    ROOT / "vendor" / "llama" / "llama-server.exe",
                    dev_gguf,
                    Path(tmp),
                    scope,
                    questions[SMOKE_IDS[1]],
                    report,
                    problems,
                )
                check_projects(client, ids, folders, questions[SMOKE_IDS[1]], report, problems)
                check_scanned(client, Path(tmp) / "Scans", report, problems)
                check_mcp(
                    client,
                    args.exe,
                    Path(tmp) / "data",
                    questions[
                        SMOKE_IDS[0]
                    ],  # th-leave-annual: hr-leave-policy.docx, Company folder
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
