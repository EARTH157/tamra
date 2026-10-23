"""The routes `Tamra.exe mcp` calls (docs/superpowers/specs/2026-10-22-tamra-mcp-design.md §5)."""

import logging
import time

import pytest
from fakes import FakeEmbedder, FakeLocalLLM, fake_spans
from fastapi.testclient import TestClient

from tamra.answer import AnswerSettings
from tamra.core import Core
from tamra.embedder import EmbedderUnavailable
from tamra.server import create_app

UI = {"X-Tamra-Token": "secret"}
MCP = {"X-Tamra-Token": "mcp-secret"}
OFF = "AI access is off. Turn it on in Tamra: Settings > General > AI apps."
LEASE = "The lease term is three years."
PARKING = "Parking costs fifty baht."


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
    client = TestClient(
        create_app("secret", None, core, mcp_token="mcp-secret"), base_url="http://127.0.0.1"
    )
    yield core, client, tmp_path
    core.shutdown()


def wait_for(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def indexed(client, project_id):
    projects = client.get("/api/projects", headers=UI).json()["projects"]
    return next(p["index"]["counts"]["indexed"] for p in projects if p["id"] == project_id)


def add_project(client, tmp_path, name, **files):
    """A project over a new folder of .md files, once every file is indexed."""
    folder = tmp_path / name
    folder.mkdir()
    for file_name, text in files.items():
        (folder / f"{file_name}.md").write_text(text, encoding="utf-8")
    response = client.post(
        "/api/projects", headers=UI, json={"name": name, "folder_path": str(folder)}
    )
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    wait_for(lambda: indexed(client, project_id) == len(files))
    return project_id


def turn_on(client):
    assert client.put("/api/settings", headers=UI, json={"mcp_enabled": True}).status_code == 200


def search(client, **body):
    return client.post("/api/mcp/search", headers=MCP, json=body)


def test_both_routes_are_refused_while_ai_access_is_off(api):
    _, client, _ = api
    for response in (client.get("/api/mcp/projects", headers=MCP), search(client, query="rent")):
        assert response.status_code == 403
        assert response.json() == {"detail": OFF}


def test_the_switch_applies_at_once(api):
    _, client, _ = api
    turn_on(client)
    assert client.get("/api/mcp/projects", headers=MCP).status_code == 200
    client.put("/api/settings", headers=UI, json={"mcp_enabled": False})
    assert client.get("/api/mcp/projects", headers=MCP).status_code == 403


def test_projects_have_names_counts_and_staleness_but_no_folders(api):
    core, client, tmp_path = api
    turn_on(client)
    leases = add_project(client, tmp_path, "Leases", lease=LEASE)
    add_project(client, tmp_path, "Parking", rules=PARKING)
    core.store.reset_index(leases, "old-model")
    assert client.get("/api/mcp/projects", headers=MCP).json() == {
        "projects": [
            {"name": "Leases", "indexed_files": 0, "stale": True},
            {"name": "Parking", "indexed_files": 1, "stale": False},
        ]
    }


def test_search_returns_project_file_location_and_text(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    add_project(client, tmp_path, "Parking", rules=PARKING)
    response = search(client, query="lease term", k=1)
    assert response.status_code == 200
    assert response.json() == {
        "passages": [
            {"project": "Leases", "file": "lease.md", "location": "line 1", "text": LEASE}
        ],
        "notices": [],
    }


def test_search_can_be_limited_to_named_projects_ignoring_case(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    add_project(client, tmp_path, "Parking", rules=PARKING)
    body = search(client, query="lease term parking baht", projects=["parking"]).json()
    assert [p["project"] for p in body["passages"]] == ["Parking"]


def test_an_unknown_project_name_is_named_back(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    response = search(client, query="lease", projects=["Nope"])
    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown project: Nope. Call list_projects for the names."}


def test_no_project_at_all(api):
    _, client, _ = api
    turn_on(client)
    response = search(client, query="lease")
    assert (response.status_code, response.json()) == (
        409,
        {"detail": "Tamra has no projects yet."},
    )


def test_a_stale_project_is_left_out_and_named(api):
    core, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    parking = add_project(client, tmp_path, "Parking", rules=PARKING)
    core.store.reset_index(parking, "old-model")
    body = search(client, query="lease term").json()
    assert body["notices"] == ["Not searched (rebuild needed): Parking"]
    assert [p["project"] for p in body["passages"]] == ["Leases"]


def test_only_stale_projects_ask_for_a_rebuild(api):
    core, client, tmp_path = api
    turn_on(client)
    leases = add_project(client, tmp_path, "Leases", lease=LEASE)
    core.store.reset_index(leases, "old-model")
    response = search(client, query="lease term")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "The index of Leases was built with a different embedding model. "
        "Rebuild it in Tamra."
    }


def test_nothing_indexed_yet(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Empty")
    response = search(client, query="lease term")
    assert response.status_code == 409
    assert response.json() == {
        "detail": "No documents are indexed yet. Wait for indexing to finish in Tamra."
    }


def test_nothing_close_enough_sends_no_passage(api):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    assert search(client, query="ใครเป็นผู้จัดการฝ่ายขาย").json() == {
        "passages": [],
        "notices": ["No passage matches this query closely enough."],
    }


def test_a_missing_search_model_is_a_503(api):
    core, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)

    def missing(text):
        raise EmbedderUnavailable("bge-m3 is not installed")

    core._embed_query = missing
    response = search(client, query="lease term")
    assert response.status_code == 503
    assert response.json() == {
        "detail": "The search model is not installed. Download it in Tamra: Settings > AI model."
    }


@pytest.mark.parametrize(
    "body",
    [
        {"query": "   "},
        {"query": "x" * 4001},
        {"query": "rent", "k": 0},
        {"query": "rent", "k": 21},
        {"query": "rent", "projects": []},
        {"query": "rent", "projects": ["p"] * 51},
        {"query": "rent", "projects": ["x" * 81]},
    ],
)
def test_bad_requests_are_refused(api, body):
    _, client, _ = api
    turn_on(client)
    assert search(client, **body).status_code == 422


def test_the_log_has_counts_but_never_the_query(api, caplog):
    _, client, tmp_path = api
    turn_on(client)
    add_project(client, tmp_path, "Leases", lease=LEASE)
    with caplog.at_level(logging.INFO, logger="tamra.core"):
        search(client, query="lease term private-words")
    assert "mcp search" in caplog.text
    assert "private-words" not in caplog.text
