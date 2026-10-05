"""The attribution, locate, page, text, and open routes (M3 task 3)."""

import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from docgen import make_docx, make_pdf
from fakes import FakeEmbedder, FakeLocalLLM, fake_spans
from fastapi.testclient import TestClient

from tamra.answer import AnswerSettings
from tamra.core import Core
from tamra.embedder import MODEL_ID
from tamra.server import create_app
from tamra.store import ChunkInput, SourceRecord

TOKEN = "secret"
AUTH = {"X-Tamra-Token": TOKEN}

LEASE = "The lease term is three years and renews automatically."
RENT = "Rent is due on the first day of each month."
DEPOSIT = "The tenant pays the deposit within seven days."
WARRANTY = "The warranty lasts ten years from purchase."

TXT_BODY = f"Intro line\n\n{LEASE}\n{RENT}\n\nClosing words here.\n"


class Env:
    """A core with a temp collection folder and helpers to index files and save answers."""

    def __init__(self, core: Core, folder: Path):
        self.core = core
        self.folder = folder
        self.store = core.store
        self.collection = self.store.replace_collection("Docs", str(folder), MODEL_ID)
        self.hashes: dict[int, str] = {}

    def add_file(self, rel_path: str, content_hash: str = "h1") -> int:
        path = self.folder / rel_path
        info = path.stat() if path.exists() else None  # as the indexer records it
        size, mtime = (info.st_size, info.st_mtime) if info else (0, 1.0)
        file_id = self.store.add_file(self.collection.id, rel_path, size, mtime)
        self.set_hash(file_id, content_hash)
        return file_id

    def set_hash(self, file_id: int, content_hash: str, texts: tuple[str, ...] = ()) -> None:
        embedder = FakeEmbedder()
        vectors = embedder.embed(list(texts)) if texts else np.zeros((0, 1024), np.float32)
        chunks = [ChunkInput(t, {"kind": "text", "line_start": 1, "line_end": 1}) for t in texts]
        self.store.replace_file_chunks(
            file_id, chunks, vectors, content_hash=content_hash, note=None
        )

    def answer(self, content: str, sources: list[SourceRecord]) -> int:
        chat = self.store.create_chat()
        self.store.add_user_message(chat.id, "question")
        return self.store.add_assistant_message(
            chat.id, content, provider="local", model="m", sources=sources
        )


def source(n, file_id, rel_path, text, location, file_hash="h1") -> SourceRecord:
    return SourceRecord(n, None, file_id, rel_path, text, location, file_hash)


def make_core(tmp_path, **overrides) -> Core:
    options = {
        "embedder_factory": FakeEmbedder,
        "token_spans_factory": lambda: fake_spans,
        "llm": FakeLocalLLM(),
        "answer_settings": AnswerSettings(min_similarity=0.3),
    }
    options.update(overrides)
    return Core(tmp_path / "data", tmp_path / "models", tmp_path / "llama.exe", **options)


@pytest.fixture
def env(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    core = make_core(tmp_path)
    yield Env(core, folder)
    core.shutdown()


class Opener:
    def __init__(self):
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> None:
        self.paths.append(path)


def client_for(env: Env, **options) -> TestClient:
    return TestClient(create_app(TOKEN, None, env.core, **options), base_url="http://127.0.0.1")


@pytest.fixture
def client(env):
    return client_for(env)


@pytest.fixture
def opener():
    return Opener()


@pytest.fixture
def txt(env):
    path = env.folder / "notes.txt"
    path.write_text(TXT_BODY, encoding="utf-8")
    file_id = env.add_file("notes.txt")
    location = {"kind": "text", "line_start": 3, "line_end": 3}
    message_id = env.answer(
        "The lease runs three years [1]. Rent is paid monthly [2].",
        [
            source(1, file_id, "notes.txt", LEASE, location),
            source(2, file_id, "notes.txt", RENT, {"kind": "text", "line_start": 4, "line_end": 4}),
        ],
    )
    return {"file_id": file_id, "message_id": message_id, "path": path}


# --- authentication ---


def test_every_new_route_needs_the_token(client):
    requests = [
        ("post", "/api/attribution", {"json": {"message_id": 1, "selection": "x"}}),
        ("get", "/api/sources/1/1/locate", {}),
        ("get", "/api/files/1/pages/1", {}),
        ("get", "/api/files/1/text", {}),
        ("post", "/api/files/1/open", {}),
    ]
    for method, url, kwargs in requests:
        assert getattr(client, method)(url, **kwargs).status_code == 401, url
        wrong = {"X-Tamra-Token": "wrong"}
        assert getattr(client, method)(url, headers=wrong, **kwargs).status_code == 401, url


def test_the_csp_allows_blob_images(client):
    csp = client.get("/api/health", headers=AUTH).headers["content-security-policy"]
    assert "img-src 'self' data: blob:" in csp


# --- attribution ---


def attribute(client, **body):
    return client.post("/api/attribution", json=body, headers=AUTH)


def test_attribution_of_a_missing_message_is_404(client):
    assert attribute(client, message_id=999, selection="x").status_code == 404


def test_attribution_of_a_user_message_is_400(env, client):
    chat = env.store.create_chat()
    env.store.add_user_message(chat.id, "a question")
    user_id = env.store.list_messages(chat.id)[0].id
    assert attribute(client, message_id=user_id, selection="a question").status_code == 400


def test_the_selection_is_stripped_and_limited(client, txt):
    message_id = txt["message_id"]
    assert attribute(client, message_id=message_id, selection="  \n ").status_code == 422
    assert attribute(client, message_id=message_id, selection="x" * 2001).status_code == 422
    assert attribute(client, message_id=message_id, selection="x" * 2000).status_code == 200
    padded = attribute(client, message_id=message_id, selection=f"  {LEASE}\n")
    assert [m["n"] for m in padded.json()["matches"]] == [1]
    assert attribute(client, message_id=message_id, selection=LEASE, n=0).status_code == 422


def test_a_strong_match_returns_the_snapshot_slice(client, txt):
    response = attribute(client, message_id=txt["message_id"], selection=LEASE)
    assert response.status_code == 200
    matches = response.json()["matches"]
    assert matches and matches[0] == {
        "n": 1,
        "start": 0,
        "end": len(LEASE),
        "text": LEASE,
        "label": "strong",
        "file_id": txt["file_id"],
        "file": "notes.txt",
        "location_label": "line 3",
        "changed": False,
    }
    assert "score" not in response.text


def test_a_chip_restricts_attribution_to_one_source(client, txt):
    response = attribute(client, message_id=txt["message_id"], selection=LEASE, n=2)
    assert all(m["n"] == 2 for m in response.json()["matches"])


def test_unrelated_text_has_no_matches(client, txt):
    response = attribute(client, message_id=txt["message_id"], selection="Quantum chromodynamics")
    assert response.json() == {"matches": []}


def test_changed_is_true_when_the_file_hash_differs(env, client, txt):
    env.set_hash(txt["file_id"], "h2")
    matches = attribute(client, message_id=txt["message_id"], selection=LEASE).json()["matches"]
    assert matches[0]["changed"] is True


def test_changed_is_true_when_the_file_left_the_index(env, client, txt):
    env.store.delete_file(txt["file_id"])
    matches = attribute(client, message_id=txt["message_id"], selection=LEASE).json()["matches"]
    assert matches[0]["changed"] is True
    assert matches[0]["file"] == "notes.txt"


def test_an_unavailable_embedder_is_a_503_with_a_fixed_message(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()

    def broken():
        raise RuntimeError(r"C:\secret\path is missing")

    core = make_core(tmp_path, embedder_factory=broken)
    try:
        env = Env(core, folder)
        (folder / "a.txt").write_text(LEASE, encoding="utf-8")
        file_id = env.add_file("a.txt")
        message_id = env.answer(
            "x [1]",
            [source(1, file_id, "a.txt", LEASE, {"kind": "text", "line_start": 1, "line_end": 1})],
        )
        response = attribute(client_for(env), message_id=message_id, selection=LEASE)
        assert response.status_code == 503
        assert "secret" not in response.text
    finally:
        core.shutdown()


TEXT_AT_1 = {"kind": "text", "line_start": 1, "line_end": 1}


def test_an_unavailable_tokenizer_is_a_503_with_a_fixed_message(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()

    def no_tokenizer():
        raise FileNotFoundError(r"C:\secret\path	okenizer.json")

    core = make_core(tmp_path, token_spans_factory=no_tokenizer)
    try:
        env = Env(core, folder)
        (folder / "a.txt").write_text(LEASE, encoding="utf-8")
        file_id = env.add_file("a.txt")
        message_id = env.answer(
            "x [1]",
            [source(1, file_id, "a.txt", LEASE, {"kind": "text", "line_start": 1, "line_end": 1})],
        )
        response = attribute(client_for(env), message_id=message_id, selection=LEASE)
        assert response.status_code == 503
        assert "secret" not in response.text
    finally:
        core.shutdown()


def test_a_repeated_sentence_credits_the_occurrence_at_start(env, client, txt):
    sentence = "Fees are charged"
    content = f"{sentence} [1]. Then something else. {sentence} [2]."
    twins = [
        source(1, txt["file_id"], "notes.txt", "Fees are charged monthly.", TEXT_AT_1),
        source(2, txt["file_id"], "notes.txt", "Fees are charged weekly.", TEXT_AT_1),
    ]
    message_id = env.answer(content, twins)
    first = attribute(client, message_id=message_id, selection=sentence).json()["matches"]
    at_first = attribute(client, message_id=message_id, selection=sentence, start=0)
    second_start = content.rfind(sentence)
    at_second = attribute(client, message_id=message_id, selection=sentence, start=second_start)
    assert first[0]["n"] == at_first.json()["matches"][0]["n"] == 1
    assert at_second.json()["matches"][0]["n"] == 2
    # a start that does not point at the selection is ignored, and a negative one is refused
    wrong = attribute(client, message_id=message_id, selection=sentence, start=7)
    assert wrong.json()["matches"][0]["n"] == 1
    assert attribute(client, message_id=message_id, selection=sentence, start=-1).status_code == 422


def test_a_start_counts_code_points_not_utf16_units(env, client, txt):
    sentence = "Fees are charged"
    content = f"😀 {sentence} [1]. 😀 {sentence} [2]."
    twins = [
        source(1, txt["file_id"], "notes.txt", "Fees are charged monthly.", TEXT_AT_1),
        source(2, txt["file_id"], "notes.txt", "Fees are charged weekly.", TEXT_AT_1),
    ]
    message_id = env.answer(content, twins)
    start = content.rfind(sentence)  # a Python (code point) index
    matches = attribute(client, message_id=message_id, selection=sentence, start=start)
    assert matches.json()["matches"][0]["n"] == 2


# --- locate ---


def locate(client, message_id, n, **params):
    return client.get(f"/api/sources/{message_id}/{n}/locate", params=params, headers=AUTH)


def test_locate_a_text_passage(client, txt):
    response = locate(client, txt["message_id"], 1)
    assert response.status_code == 200
    assert response.json() == {
        "file_id": txt["file_id"],
        "kind": "text",
        "changed": False,
        "found": True,
        "start": 3,
        "end": 4,
    }


def test_locate_a_sub_range(client, txt):
    start = LEASE.index("three years")
    body = locate(client, txt["message_id"], 1, start=start, end=len(LEASE)).json()
    assert (body["found"], body["start"], body["end"]) == (True, 3, 4)


def test_locate_validates_the_range(client, txt):
    message_id = txt["message_id"]
    assert locate(client, message_id, 1, start=5).status_code == 400
    assert locate(client, message_id, 1, end=5).status_code == 400
    assert locate(client, message_id, 1, start=5, end=5).status_code == 400
    assert locate(client, message_id, 1, start=-1, end=5).status_code == 400
    assert locate(client, message_id, 1, start=0, end=len(LEASE) + 1).status_code == 400


def test_locate_a_docx_passage(env, client):
    make_docx(
        env.folder / "lease.docx",
        [(1, "Lease"), (0, "Intro paragraph here."), (0, DEPOSIT), (0, "Closing paragraph.")],
    )
    file_id = env.add_file("lease.docx")
    message_id = env.answer(
        "Seven days [1].",
        [
            source(
                1,
                file_id,
                "lease.docx",
                DEPOSIT,
                {"kind": "docx", "paragraph_start": 2, "paragraph_end": 2, "heading_path": []},
            )
        ],
    )
    body = locate(client, message_id, 1).json()
    assert (body["kind"], body["found"], body["start"], body["end"]) == ("docx", True, 2, 3)
    assert "rects" not in body and "page" not in body


def test_locate_a_pdf_passage_with_rects(env, client):
    make_pdf(env.folder / "w.pdf", [["Page one is about cats."], [WARRANTY, "A second line."]])
    file_id = env.add_file("w.pdf")
    message_id = env.answer(
        "Ten years [1].",
        [
            source(
                1,
                file_id,
                "w.pdf",
                WARRANTY,
                {"kind": "pdf", "page_start": 2, "page_end": 2, "char_start": 0, "char_end": 41},
            )
        ],
    )
    body = locate(client, message_id, 1).json()
    assert (body["kind"], body["found"], body["page"], body["page_count"]) == ("pdf", True, 2, 2)
    assert body["rects"]
    for rect in body["rects"]:
        assert len(rect) == 4 and all(0.0 <= v <= 1.0 for v in rect)
        assert rect[0] < rect[2] and rect[1] < rect[3]
    assert body["start"] < body["end"]


def test_locate_is_not_found_when_the_passage_left_the_file(env, client, txt):
    txt["path"].write_text("Completely different words now, nothing like before.\n", "utf-8")
    body = locate(client, txt["message_id"], 1).json()
    # edited on disk but not yet re-indexed: the stored size no longer matches
    assert body == {"file_id": txt["file_id"], "kind": "text", "changed": True, "found": False}


def test_locate_reports_a_changed_file(env, client, txt):
    env.set_hash(txt["file_id"], "h2")
    body = locate(client, txt["message_id"], 1).json()
    assert body["changed"] is True and body["found"] is True


def test_locate_a_pdf_that_lost_the_passage_still_has_a_page_count(env, client):
    make_pdf(env.folder / "w.pdf", [["Nothing relevant on this page at all."]])
    file_id = env.add_file("w.pdf")
    message_id = env.answer(
        "x [1]",
        [source(1, file_id, "w.pdf", WARRANTY, {"kind": "pdf", "page_start": 1, "page_end": 1})],
    )
    body = locate(client, message_id, 1).json()
    assert body["found"] is False and body["page_count"] == 1
    assert "page" not in body and "rects" not in body


def test_locate_says_which_thing_is_missing(env, client, txt):
    assert locate(client, 999, 1).json()["detail"] == "Source not found."
    assert locate(client, txt["message_id"], 9).json()["detail"] == "Source not found."
    txt["path"].unlink()
    response = locate(client, txt["message_id"], 1)
    assert response.status_code == 404 and response.json()["detail"] == "File not found."
    env.store.delete_file(txt["file_id"])
    response = locate(client, txt["message_id"], 1)
    assert response.status_code == 404 and response.json()["detail"] == "File not found."


def test_a_damaged_pdf_is_a_422(env, client):
    (env.folder / "bad.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    file_id = env.add_file("bad.pdf")
    message_id = env.answer(
        "x [1]",
        [source(1, file_id, "bad.pdf", WARRANTY, {"kind": "pdf", "page_start": 1, "page_end": 1})],
    )
    response = locate(client, message_id, 1)
    assert response.status_code == 422
    assert response.json()["detail"] == "This PDF could not be read."
    assert client.get(f"/api/files/{file_id}/pages/1", headers=AUTH).status_code == 422


def test_a_broken_docx_in_a_folder_named_pdfs_is_not_called_a_pdf(tmp_path):
    folder = tmp_path / "My PDFs"
    folder.mkdir()
    core = make_core(tmp_path)
    try:
        env = Env(core, folder)
        (folder / "broken.docx").write_bytes(b"this is not a zip file")
        file_id = env.add_file("broken.docx")
        message_id = env.answer(
            "x [1]",
            [
                source(
                    1,
                    file_id,
                    "broken.docx",
                    DEPOSIT,
                    {"kind": "docx", "paragraph_start": 0, "paragraph_end": 0},
                )
            ],
        )
        client = client_for(env)
        for response in (
            locate(client, message_id, 1),
            client.get(f"/api/files/{file_id}/text", headers=AUTH),
        ):
            assert response.status_code == 422
            assert response.json()["detail"] == "This file could not be read."
    finally:
        core.shutdown()


@pytest.mark.parametrize("name", ["locked.txt", "locked.pdf"])
def test_an_os_error_while_parsing_is_a_422_not_a_500(env, client, monkeypatch, name):
    import tamra.core as core_module

    (env.folder / name).write_bytes(b"x")
    file_id = env.add_file(name)
    message_id = env.answer("x [1]", [source(1, file_id, name, LEASE, TEXT_AT_1)])

    def denied(path):
        raise PermissionError(r"C:\secret\locked: access denied")

    monkeypatch.setattr(core_module, "parse_file", denied)
    response = locate(client, message_id, 1)
    assert response.status_code == 422
    kind = "PDF" if name.endswith(".pdf") else "file"
    assert response.json()["detail"] == f"This {kind} could not be read."
    assert "secret" not in response.text


def test_a_file_removed_during_parsing_is_a_404(env, client, monkeypatch):
    import tamra.core as core_module

    (env.folder / "gone.txt").write_text("x", encoding="utf-8")
    file_id = env.add_file("gone.txt")

    def vanished(path):
        raise FileNotFoundError(str(path))

    monkeypatch.setattr(core_module, "parse_file", vanished)
    response = client.get(f"/api/files/{file_id}/text", headers=AUTH)
    assert response.status_code == 404 and response.json()["detail"] == "File not found."


def test_a_missing_page_says_page_not_found(client, pdf):
    assert page(client, pdf, 9).json()["detail"] == "Page not found."


def test_a_file_edited_since_it_was_indexed_is_changed(env, client, txt):
    assert locate(client, txt["message_id"], 1).json()["changed"] is False
    path = txt["path"]
    path.write_text(TXT_BODY + "More words.\n", encoding="utf-8")
    assert locate(client, txt["message_id"], 1).json()["changed"] is True
    matches = attribute(client, message_id=txt["message_id"], selection=LEASE).json()["matches"]
    assert matches[0]["changed"] is True


def test_a_file_with_only_a_new_mtime_is_changed(env, client, txt):
    stat = txt["path"].stat()
    os.utime(txt["path"], (stat.st_atime, stat.st_mtime + 100))
    assert locate(client, txt["message_id"], 1).json()["changed"] is True


def test_a_malformed_location_hint_does_not_break_locate(env, client):
    path = env.folder / "n.txt"
    path.write_text(TXT_BODY, encoding="utf-8")
    file_id = env.add_file("n.txt")
    message_id = env.answer("x [1]", [source(1, file_id, "n.txt", LEASE, {"kind": "text"})])
    body = locate(client, message_id, 1).json()
    assert (body["found"], body["start"], body["end"]) == (True, 3, 4)


def test_other_attribution_failures_are_a_500_and_logged_with_a_traceback(env, txt, caplog):
    def boom(*args, **kwargs):
        raise ValueError("a bug")

    env.core.attribute = boom
    client = TestClient(
        create_app(TOKEN, None, env.core),
        base_url="http://127.0.0.1",
        raise_server_exceptions=False,
    )
    with caplog.at_level(logging.ERROR, logger="tamra.server"):
        response = attribute(client, message_id=txt["message_id"], selection=LEASE)
    assert response.status_code == 500
    assert any(r.exc_info and r.exc_info[0] is ValueError for r in caplog.records)


# --- the parsed document cache ---


def test_a_document_is_parsed_once_until_it_changes(env, client, txt, monkeypatch):
    import tamra.core as core_module

    parsed = []
    real = core_module.parse_file
    monkeypatch.setattr(core_module, "parse_file", lambda p: parsed.append(p) or real(p))
    for _ in range(3):
        assert locate(client, txt["message_id"], 1).json()["found"] is True
    assert len(parsed) == 1
    txt["path"].write_text(TXT_BODY + "One more line.\n", encoding="utf-8")
    assert locate(client, txt["message_id"], 1).json()["found"] is True
    assert len(parsed) == 2


def test_the_document_cache_keeps_eight_files(env):
    env.core._documents.clear()
    ids = []
    for i in range(10):
        (env.folder / f"f{i}.txt").write_text(f"File number {i} has some words.", "utf-8")
        ids.append(env.add_file(f"f{i}.txt"))
    for file_id in ids:
        env.core.document(file_id)
    assert len(env.core._documents) == 8
    assert {key[0] for key in env.core._documents} == set(ids[2:])


# --- page images ---


def page(client, file_id, number, **params):
    return client.get(f"/api/files/{file_id}/pages/{number}", params=params, headers=AUTH)


@pytest.fixture
def pdf(env):
    make_pdf(env.folder / "w.pdf", [["Page one."], [WARRANTY]])
    return env.add_file("w.pdf")


def test_a_page_is_a_png(client, pdf):
    response = page(client, pdf, 1)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    larger = page(client, pdf, 1, scale=3.0)
    assert larger.content != response.content


def test_pages_out_of_range_are_404(client, pdf):
    assert page(client, pdf, 0).status_code == 404
    assert page(client, pdf, 3).status_code == 404
    assert page(client, 999, 1).status_code == 404


def test_a_scale_outside_the_range_is_400(client, pdf):
    for scale in ("0.4", "3.1", "0", "-1", "nan", "inf"):
        assert page(client, pdf, 1, scale=scale).status_code == 400, scale
    assert page(client, pdf, 1, scale=0.5).status_code == 200


def test_a_text_file_has_no_pages(client, txt):
    assert page(client, txt["file_id"], 1).status_code == 404


# --- text view ---


def test_the_text_of_a_txt_file(client, txt):
    response = client.get(f"/api/files/{txt['file_id']}/text", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "kind": "text",
        "lines": ["Intro line", "", LEASE, RENT, "", "Closing words here."],
    }


def test_the_text_of_a_docx_file(env, client):
    make_docx(env.folder / "lease.docx", [(1, "Lease"), (0, DEPOSIT)])
    file_id = env.add_file("lease.docx")
    body = client.get(f"/api/files/{file_id}/text", headers=AUTH).json()
    assert body["kind"] == "docx"
    assert body["paragraphs"] == [
        {"index": 0, "text": "Lease", "heading": True},
        {"index": 1, "text": DEPOSIT, "heading": False},
    ]


def test_a_pdf_has_no_text_view(client, pdf):
    assert client.get(f"/api/files/{pdf}/text", headers=AUTH).status_code == 400


def test_the_text_of_a_missing_file_is_404(client, txt):
    assert client.get("/api/files/999/text", headers=AUTH).status_code == 404
    txt["path"].unlink()
    assert client.get(f"/api/files/{txt['file_id']}/text", headers=AUTH).status_code == 404


# --- open ---


def open_file(client, file_id):
    return client.post(f"/api/files/{file_id}/open", headers=AUTH)


def test_open_is_501_without_an_opener(client, txt):
    assert open_file(client, txt["file_id"]).status_code == 501


def test_open_calls_the_opener_with_the_resolved_path(env, opener, txt):
    response = open_file(client_for(env, open_external=opener), txt["file_id"])
    assert response.status_code == 204 and response.content == b""
    assert opener.paths == [txt["path"].resolve()]
    assert opener.paths[0].is_relative_to(env.folder.resolve())


def test_open_never_takes_a_path_from_the_request(env, opener, txt):
    client = client_for(env, open_external=opener)
    response = client.post(
        f"/api/files/{txt['file_id']}/open",
        params={"path": r"C:\Windows\notepad.exe"},
        json={"path": r"C:\Windows\notepad.exe"},
        headers=AUTH,
    )
    assert response.status_code == 204
    assert opener.paths == [txt["path"].resolve()]


def test_open_refuses_a_path_outside_the_folder(env, opener, tmp_path):
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    file_id = env.add_file("..\\outside.txt")
    client = client_for(env, open_external=opener)
    response = open_file(client, file_id)
    assert response.status_code == 400
    assert "outside" not in response.text  # a fixed message: no raw path or reason
    assert opener.paths == []
    assert client.get(f"/api/files/{file_id}/text", headers=AUTH).status_code == 400
    assert page(client, file_id, 1).status_code == 400


def test_open_refuses_a_file_that_is_not_a_document(env, opener):
    (env.folder / "tool.exe").write_bytes(b"MZ")
    file_id = env.add_file("tool.exe")
    assert open_file(client_for(env, open_external=opener), file_id).status_code == 400
    assert opener.paths == []


def test_open_of_a_missing_file_is_404(env, opener, txt):
    client = client_for(env, open_external=opener)
    assert open_file(client, 999).status_code == 404
    txt["path"].unlink()
    assert open_file(client, txt["file_id"]).status_code == 404
    assert opener.paths == []


def test_a_failing_opener_is_a_500_with_a_fixed_message(env, txt):
    def broken(path):
        raise OSError(f"cannot start {path}")

    response = open_file(client_for(env, open_external=broken), txt["file_id"])
    assert response.status_code == 500
    assert response.json() == {"detail": "Could not open the file."}


# --- sources carry the file id; the attribution cache is warmed ---


def test_source_payload_has_the_file_id(env, client, txt):
    chat_id = env.store.list_chats()[0].id
    messages = client.get(f"/api/chats/{chat_id}", headers=AUTH).json()["messages"]
    assert [s["file_id"] for s in messages[1]["sources"]] == [txt["file_id"]] * 2


def indexed_lease(env: Env) -> tuple[int, int]:
    (env.folder / "lease.txt").write_text(LEASE, encoding="utf-8")
    file_id = env.add_file("lease.txt")
    env.set_hash(file_id, "h1", texts=(LEASE,))
    return file_id, env.store.create_chat().id


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


def test_a_saved_answer_warms_the_attribution_cache(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    core = make_core(tmp_path, warm_attribution=True)
    try:
        env = Env(core, folder)
        _, chat_id = indexed_lease(env)
        events = list(core.answers.ask(chat_id, "How long is the lease term?"))
        assert events[-1]["type"] == "done"
        embedder = core.embedder()
        wait_until(lambda: embedder.calls >= 2)
        time.sleep(0.1)
        assert embedder.calls == 2  # the question, then every source's windows: no selection
        calls = embedder.calls
        message = core.store.get_message(events[-1]["message_id"])
        assert [m.n for m in core.attribute(message, LEASE)] == [1]
        assert embedder.calls == calls + 1  # only the selection: the windows were cached
    finally:
        core.shutdown()


def test_the_cache_is_not_warmed_unless_enabled(env):
    _, chat_id = indexed_lease(env)
    events = list(env.core.answers.ask(chat_id, "How long is the lease term?"))
    assert events[-1]["type"] == "done"
    time.sleep(0.2)
    assert env.core.embedder().calls == 1  # only the question


def test_a_failed_warm_up_is_only_logged_and_leaves_the_answer_alone(tmp_path, caplog):
    folder = tmp_path / "docs"
    folder.mkdir()

    class Flaky(FakeEmbedder):
        broken = True

        def embed(self, texts, batch_size=16):
            if self.broken and not texts[0].startswith("How"):
                raise RuntimeError("model went away")
            return super().embed(texts)

    core = make_core(tmp_path, embedder_factory=Flaky, warm_attribution=True)
    try:
        env = Env(core, folder)
        _, chat_id = indexed_lease(env)
        with caplog.at_level(logging.WARNING, logger="tamra.core"):
            events = list(core.answers.ask(chat_id, "How long is the lease term?"))
            wait_until(lambda: "warm-up failed: RuntimeError" in caplog.text)
        assert [e["type"] for e in events if e["type"] == "error"] == []
        assert events[-1]["type"] == "done"
        message = core.store.get_message(events[-1]["message_id"])
        assert message is not None and len(message.sources) == 1  # the answer was saved intact
        core.embedder().broken = False  # the failure poisoned nothing: it works afterwards
        assert [m.n for m in core.attribute(message, LEASE)] == [1]
        core._warm_now(999)  # a message that does not exist is not an error
    finally:
        core.shutdown()


def test_nothing_is_warmed_after_shutdown(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    core = make_core(tmp_path, warm_attribution=True)
    env = Env(core, folder)
    _, chat_id = indexed_lease(env)
    message_id = env.answer("x [1]", [source(1, 1, "lease.txt", LEASE, {"kind": "text"})])
    embedder = core.embedder()
    calls = embedder.calls
    core.shutdown()
    core._warm(message_id)  # the pool is closed: this must not raise or embed
    core._warm_now(message_id)
    time.sleep(0.1)
    assert embedder.calls == calls and chat_id


def test_warming_uses_one_named_worker(tmp_path):
    core = make_core(tmp_path, warm_attribution=True)
    try:
        assert core._warm_pool._max_workers == 1
        assert core._warm_pool._thread_name_prefix == "tamra-attribution-warm"
    finally:
        core.shutdown()


# --- size caps and links ---


def test_a_long_text_view_is_cut_and_says_so(env, client, monkeypatch):
    import tamra.viewer as viewer

    (env.folder / "long.txt").write_text("\n".join(f"line {i}" for i in range(30)), "utf-8")
    file_id = env.add_file("long.txt")
    monkeypatch.setattr(viewer, "MAX_TEXT_LINES", 12)
    body = client.get(f"/api/files/{file_id}/text", headers=AUTH).json()
    assert len(body["lines"]) == 12 and body["truncated"] is True


def test_a_huge_file_is_parsed_but_not_cached(env, client, txt, monkeypatch):
    import tamra.core as core_module

    monkeypatch.setattr(core_module, "DOCUMENT_CACHE_MAX_BYTES", 10)
    env.core._documents.clear()
    assert locate(client, txt["message_id"], 1).json()["found"] is True
    assert len(env.core._documents) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions")
def test_a_junction_out_of_the_folder_is_refused(env, opener, tmp_path):
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (outside / "secret.pdf").write_bytes(b"%PDF-1.4")
    _winapi.CreateJunction(str(outside), str(env.folder / "link"))
    client = client_for(env, open_external=opener)
    for name in ("secret.txt", "secret.pdf"):
        file_id = env.add_file(f"link/{name}")
        assert open_file(client, file_id).status_code == 400
        assert client.get(f"/api/files/{file_id}/text", headers=AUTH).status_code == 400
        assert page(client, file_id, 1).status_code == 400
    assert opener.paths == []
