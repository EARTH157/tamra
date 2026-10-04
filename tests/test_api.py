import json
import time

import pytest
from fakes import FakeEmbedder, FakeLocalLLM, fake_spans
from fastapi.testclient import TestClient

from tamra.answer import AnswerSettings
from tamra.core import Core
from tamra.server import create_app

TOKEN = "secret"
AUTH = {"X-Tamra-Token": TOKEN}


@pytest.fixture
def api(tmp_path):
    core = Core(
        tmp_path / "data",
        tmp_path / "models",
        tmp_path / "llama.exe",
        embedder_factory=FakeEmbedder,
        token_spans_factory=lambda: fake_spans,
        llm=FakeLocalLLM(),
        answer_settings=AnswerSettings(min_similarity=0.3),
        debounce=0.2,
    )
    core.start()
    client = TestClient(create_app(TOKEN, None, core), base_url="http://127.0.0.1")
    yield client, tmp_path
    core.shutdown()


def events_of(response):
    return [
        json.loads(line[len("data:") :])
        for line in response.iter_lines()
        if line.startswith("data:")
    ]


def wait_for_indexed(client, count, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        index = client.get("/api/collection", headers=AUTH).json()["index"]
        if index and index["counts"]["indexed"] == count:
            return index
        time.sleep(0.05)
    raise AssertionError("indexing did not finish")


def test_choose_a_folder_ask_and_review_the_chat(api):
    client, tmp_path = api
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "lease.md").write_text("The lease term is three years.", encoding="utf-8")
    empty = client.get("/api/collection", headers=AUTH).json()
    assert empty == {"collection": None, "index": None}
    put = client.put(
        "/api/collection", headers=AUTH, json={"name": "Docs", "folder_path": str(docs)}
    )
    assert put.status_code == 200
    assert put.json()["collection"]["name"] == "Docs"
    index = wait_for_indexed(client, 1)
    assert index["stale"] is False

    chat = client.post("/api/chats", headers=AUTH)
    assert chat.status_code == 201
    chat_id = chat.json()["id"]
    with client.stream(
        "POST",
        f"/api/chats/{chat_id}/messages",
        headers=AUTH,
        json={"content": "How long is the lease term?"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = events_of(response)
    assert events[0]["type"] == "sources"
    assert events[0]["sources"][0]["file"] == "lease.md"
    assert events[-1]["type"] == "done"

    detail = client.get(f"/api/chats/{chat_id}", headers=AUTH).json()
    assert detail["title"] == "How long is the lease term?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["sources"][0]["label"] == "line 1"
    assert client.get("/api/chats", headers=AUTH).json()["chats"][0]["id"] == chat_id
    assert client.delete(f"/api/chats/{chat_id}", headers=AUTH).status_code == 204
    assert client.get(f"/api/chats/{chat_id}", headers=AUTH).status_code == 404
    assert client.delete(f"/api/chats/{chat_id}", headers=AUTH).status_code == 404


def test_a_bad_folder_is_a_400(api):
    client, tmp_path = api
    response = client.put(
        "/api/collection", headers=AUTH, json={"name": "x", "folder_path": str(tmp_path / "nope")}
    )
    assert response.status_code == 400
    assert "Folder not found" in response.json()["detail"]


def test_rebuild_and_cancel(api):
    client, tmp_path = api
    assert client.post("/api/collection/rebuild", headers=AUTH).status_code == 404
    docs = tmp_path / "docs"
    docs.mkdir()
    client.put("/api/collection", headers=AUTH, json={"name": "d", "folder_path": str(docs)})
    assert client.post("/api/collection/rebuild", headers=AUTH).status_code == 202
    assert client.post("/api/answer/cancel", headers=AUTH).status_code == 204


def test_an_empty_question_is_rejected(api):
    client, _ = api
    chat_id = client.post("/api/chats", headers=AUTH).json()["id"]
    response = client.post(f"/api/chats/{chat_id}/messages", headers=AUTH, json={"content": ""})
    assert response.status_code == 422


def test_data_routes_need_the_token(api):
    client, _ = api
    assert client.get("/api/chats").status_code == 401
    assert client.post("/api/chats/1/messages", json={"content": "q"}).status_code == 401
