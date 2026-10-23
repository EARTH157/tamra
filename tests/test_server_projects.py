"""The project, chat-home and search-scope routes (M4 task 4)."""

import time

import pytest
from docgen import make_pdf
from fakes import FakeEmbedder, FakeLLM, FakeLocalLLM, fake_spans
from fastapi.testclient import TestClient

from tamra.answer import AnswerSettings
from tamra.core import Core
from tamra.server import create_app

TOKEN = "secret"
AUTH = {"X-Tamra-Token": TOKEN}
INDEX_KEYS = {"counts", "stale", "current", "error", "reading", "problems"}


def make_api(tmp_path, llm=None):
    core = Core(
        tmp_path / "data",
        tmp_path / "models",
        tmp_path / "llama.exe",
        embedder_factory=FakeEmbedder,
        token_spans_factory=lambda: fake_spans,
        llm=llm or FakeLocalLLM(),
        answer_settings=AnswerSettings(min_similarity=0.3),
        debounce=0.2,
    )
    core.start()
    return core, TestClient(create_app(TOKEN, None, core), base_url="http://127.0.0.1")


@pytest.fixture
def api(tmp_path):
    core, client = make_api(tmp_path)
    yield client, tmp_path
    core.shutdown()


def folder(tmp_path, name, **files):
    path = tmp_path / name
    path.mkdir()
    for file_name, text in files.items():
        (path / f"{file_name}.md").write_text(text, encoding="utf-8")
    return path


def add_project(client, name, path):
    response = client.post(
        "/api/projects", headers=AUTH, json={"name": name, "folder_path": str(path)}
    )
    assert response.status_code == 201, response.text
    return response.json()


def wait_for(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def project(client, project_id):
    projects = client.get("/api/projects", headers=AUTH).json()["projects"]
    return next((p for p in projects if p["id"] == project_id), None)


# --- projects ---


def test_no_projects_at_first(api):
    client, _ = api
    assert client.get("/api/projects", headers=AUTH).json() == {"projects": []}


def test_create_a_project_returns_it_with_its_index_status(api):
    client, tmp_path = api
    docs = folder(tmp_path, "docs", lease="The lease term is three years.")
    created = client.post(
        "/api/projects", headers=AUTH, json={"name": "  Lease  ", "folder_path": str(docs)}
    )
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {"id", "name", "folder_path", "index"}
    assert (body["name"], body["folder_path"]) == ("Lease", str(docs.resolve()))
    assert set(body["index"]) == INDEX_KEYS
    assert body["index"]["stale"] is False
    wait_for(lambda: project(client, body["id"])["index"]["counts"]["indexed"] == 1)
    listed = client.get("/api/projects", headers=AUTH).json()
    assert [p["id"] for p in listed["projects"]] == [body["id"]]
    assert set(listed["projects"][0]["index"]["counts"]) >= {"indexed", "pending", "failed"}
    assert listed["projects"][0]["index"]["reading"] is None


def test_a_scan_waiting_for_ocr_is_a_problem_that_says_so(api):
    client, tmp_path = api  # the temp models folder has no OCR models
    docs = tmp_path / "scans"
    make_pdf(docs / "scan.pdf", [["Cover."], []])
    created = add_project(client, "Scans", docs)
    wait_for(lambda: project(client, created["id"])["index"]["counts"]["indexed"] == 1)
    index = project(client, created["id"])["index"]
    assert index["reading"] is None
    assert index["problems"] == [
        {
            "rel_path": "scan.pdf",
            "status": "indexed",
            "error": "no text layer on page(s) 2 (needs OCR)",
            "needs_ocr": True,
        }
    ]


def test_a_project_without_a_name_is_named_after_its_folder(api):
    client, tmp_path = api
    docs = folder(tmp_path, "My Docs")
    response = client.post("/api/projects", headers=AUTH, json={"folder_path": str(docs)})
    assert response.status_code == 201
    assert response.json()["name"] == "My Docs"


@pytest.mark.parametrize("name", ["", "   ", "x" * 81, " " + "x" * 81 + " "])
def test_a_blank_or_long_project_name_is_a_400(api, name):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    response = client.post(
        "/api/projects", headers=AUTH, json={"name": name, "folder_path": str(docs)}
    )
    assert response.status_code == 400
    assert client.get("/api/projects", headers=AUTH).json() == {"projects": []}


def test_an_80_character_name_is_accepted_after_a_strip(api):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    name = "x" * 80
    response = client.post(
        "/api/projects", headers=AUTH, json={"name": f"  {name} ", "folder_path": str(docs)}
    )
    assert response.status_code == 201
    assert response.json()["name"] == name


def test_a_bad_folder_is_a_400_without_the_path(api):
    client, tmp_path = api
    missing = client.post(
        "/api/projects", headers=AUTH, json={"name": "x", "folder_path": str(tmp_path / "nope")}
    )
    assert missing.status_code == 400
    assert missing.json() == {"detail": "Folder not found."}
    (tmp_path / "plain.txt").write_text("x", encoding="utf-8")
    not_a_folder = client.post(
        "/api/projects",
        headers=AUTH,
        json={"name": "x", "folder_path": str(tmp_path / "plain.txt")},
    )
    assert not_a_folder.status_code == 400
    relative = client.post(
        "/api/projects", headers=AUTH, json={"name": "x", "folder_path": "relative/dir"}
    )
    assert relative.status_code == 400
    assert "relative" not in relative.json()["detail"]
    assert client.get("/api/projects", headers=AUTH).json() == {"projects": []}


def test_a_long_folder_name_gives_a_name_cut_to_80_characters(api):
    client, tmp_path = api
    docs = folder(tmp_path, "d" * 120)
    response = client.post("/api/projects", headers=AUTH, json={"folder_path": str(docs)})
    assert response.status_code == 201
    assert response.json()["name"] == "d" * 80


def test_a_folder_name_with_a_bidi_override_gives_a_clean_name(api):
    client, tmp_path = api
    try:
        docs = folder(tmp_path, "gnp\u202e.md  notes")
    except OSError:
        pytest.skip("the file system refuses this folder name")
    response = client.post("/api/projects", headers=AUTH, json={"folder_path": str(docs)})
    assert response.status_code == 201
    assert response.json()["name"] == "gnp.md notes"


@pytest.mark.parametrize(
    "name", ["a\x00b", "a\x1bb", "\u202egnp.exe", "zero\u200bwidth", "a\u2066b"]
)
def test_control_and_format_characters_in_a_name_are_a_400(api, name):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    response = client.post(
        "/api/projects", headers=AUTH, json={"name": name, "folder_path": str(docs)}
    )
    assert response.status_code == 400
    assert "control or format" in response.json()["detail"]
    assert client.get("/api/projects", headers=AUTH).json() == {"projects": []}
    kept = add_project(client, "Kept", folder(tmp_path, "kept"))
    renamed = client.patch(f"/api/projects/{kept['id']}", headers=AUTH, json={"name": name})
    assert renamed.status_code == 400
    assert project(client, kept["id"])["name"] == "Kept"


def test_a_name_has_its_whitespace_collapsed(api):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    response = client.post(
        "/api/projects",
        headers=AUTH,
        json={"name": "  My \t  lease\n docs ", "folder_path": str(docs)},
    )
    assert response.json()["name"] == "My lease docs"
    renamed = client.patch(
        f"/api/projects/{response.json()['id']}", headers=AUTH, json={"name": "A    B"}
    )
    assert renamed.json()["name"] == "A B"


def test_a_name_longer_than_80_after_collapsing_is_a_400(api):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    name = " ".join(["word"] * 17)  # 84 characters
    response = client.post(
        "/api/projects", headers=AUTH, json={"name": name, "folder_path": str(docs)}
    )
    assert response.status_code == 400


NOT_ALLOWED = {"detail": "Choose a folder of documents, not a whole drive or Tamra's own folder."}


def test_a_drive_root_is_refused(api):
    client, tmp_path = api
    response = client.post(
        "/api/projects", headers=AUTH, json={"name": "All", "folder_path": tmp_path.anchor}
    )
    assert response.status_code == 400
    assert response.json() == NOT_ALLOWED


def test_tamras_own_folders_and_their_parents_are_refused(api):
    client, tmp_path = api
    (tmp_path / "models").mkdir(exist_ok=True)
    inside_data = tmp_path / "data" / "inner"
    inside_data.mkdir()
    for refused in (
        tmp_path / "data",  # the data folder
        inside_data,  # inside it
        tmp_path,  # a parent of the data and models folders
        tmp_path / "models",
    ):
        response = client.post(
            "/api/projects", headers=AUTH, json={"name": "Own", "folder_path": str(refused)}
        )
        assert response.status_code == 400, refused
        assert response.json() == NOT_ALLOWED
    assert client.get("/api/projects", headers=AUTH).json() == {"projects": []}


def test_a_normal_subfolder_next_to_tamras_is_allowed(api):
    client, tmp_path = api
    nested = folder(tmp_path, "docs") / "deeper"
    nested.mkdir()
    response = client.post("/api/projects", headers=AUTH, json={"folder_path": str(nested)})
    assert response.status_code == 201


def test_changing_a_folder_to_a_refused_one_is_a_400(api):
    client, tmp_path = api
    created = add_project(client, "Docs", folder(tmp_path, "docs"))
    for refused in (tmp_path.anchor, str(tmp_path / "data")):
        response = client.put(
            f"/api/projects/{created['id']}/folder", headers=AUTH, json={"folder_path": refused}
        )
        assert response.status_code == 400
        assert response.json() == NOT_ALLOWED


def test_an_unexpected_value_error_is_a_generic_400_without_its_text(tmp_path):
    core, client = make_api(tmp_path)
    try:

        def explode(name, folder):
            raise ValueError("C:\\Users\\secret\\path")

        core.create_project = explode
        response = client.post(
            "/api/projects", headers=AUTH, json={"name": "x", "folder_path": str(tmp_path)}
        )
        assert response.status_code == 400
        assert response.json() == {"detail": "The project could not be changed."}
    finally:
        core.shutdown()


def test_an_empty_folder_path_is_rejected(api):
    client, _ = api
    response = client.post("/api/projects", headers=AUTH, json={"name": "x", "folder_path": ""})
    assert response.status_code == 422


def test_a_duplicate_name_is_a_409_whatever_its_case(api):
    client, tmp_path = api
    add_project(client, "Docs", folder(tmp_path, "one"))
    other = folder(tmp_path, "two")
    response = client.post(
        "/api/projects", headers=AUTH, json={"name": "docs", "folder_path": str(other)}
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "Another project already has this name."}
    assert len(client.get("/api/projects", headers=AUTH).json()["projects"]) == 1


def test_a_folder_used_by_another_project_is_a_409(api):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    add_project(client, "One", docs)
    same = client.post(
        "/api/projects", headers=AUTH, json={"name": "Two", "folder_path": str(docs)}
    )
    assert same.status_code == 409
    assert same.json() == {"detail": "This folder is already used by another project."}
    nested = docs / "inner"
    nested.mkdir()
    inside = client.post(
        "/api/projects", headers=AUTH, json={"name": "Three", "folder_path": str(nested)}
    )
    assert inside.status_code == 409


def test_rename_a_project(api):
    client, tmp_path = api
    created = add_project(client, "Old", folder(tmp_path, "docs"))
    renamed = client.patch(f"/api/projects/{created['id']}", headers=AUTH, json={"name": " New "})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "New"
    assert set(renamed.json()) == {"id", "name", "folder_path", "index"}
    assert project(client, created["id"])["name"] == "New"
    same = client.patch(f"/api/projects/{created['id']}", headers=AUTH, json={"name": "new"})
    assert same.status_code == 200  # only its own name differs in case


def test_rename_errors(api):
    client, tmp_path = api
    one = add_project(client, "One", folder(tmp_path, "one"))
    add_project(client, "Two", folder(tmp_path, "two"))
    path = f"/api/projects/{one['id']}"
    assert client.patch(path, headers=AUTH, json={"name": "two"}).status_code == 409
    assert client.patch(path, headers=AUTH, json={"name": "  "}).status_code == 400
    assert client.patch(path, headers=AUTH, json={"name": "x" * 81}).status_code == 400
    assert client.patch("/api/projects/999", headers=AUTH, json={"name": "x"}).status_code == 404
    assert client.patch(path, headers=AUTH, json={}).json()["name"] == "One"  # nothing to change
    assert project(client, one["id"])["name"] == "One"


def test_change_a_projects_folder(api):
    client, tmp_path = api
    old = folder(tmp_path, "old", a="Lease terms.")
    new = folder(tmp_path, "new", b="Parking rules.", c="Pets welcome.")
    created = add_project(client, "Docs", old)
    wait_for(lambda: project(client, created["id"])["index"]["counts"]["indexed"] == 1)
    response = client.put(
        f"/api/projects/{created['id']}/folder", headers=AUTH, json={"folder_path": str(new)}
    )
    assert response.status_code == 200
    assert response.json()["folder_path"] == str(new.resolve())
    assert response.json()["name"] == "Docs"
    wait_for(lambda: project(client, created["id"])["index"]["counts"]["indexed"] == 2)


def test_change_folder_errors(api):
    client, tmp_path = api
    one = add_project(client, "One", folder(tmp_path, "one"))
    two_folder = folder(tmp_path, "two")
    add_project(client, "Two", two_folder)
    path = f"/api/projects/{one['id']}/folder"
    taken = client.put(path, headers=AUTH, json={"folder_path": str(two_folder)})
    assert taken.status_code == 409
    missing = client.put(path, headers=AUTH, json={"folder_path": str(tmp_path / "nope")})
    assert missing.status_code == 400 and missing.json() == {"detail": "Folder not found."}
    unknown = client.put(
        "/api/projects/999/folder", headers=AUTH, json={"folder_path": str(two_folder)}
    )
    assert unknown.status_code == 404
    assert client.put(path, headers=AUTH, json={"folder_path": ""}).status_code == 422


def test_rebuild_a_project(api):
    client, tmp_path = api
    created = add_project(client, "Docs", folder(tmp_path, "docs", a="Lease terms."))
    wait_for(lambda: project(client, created["id"])["index"]["counts"]["indexed"] == 1)
    response = client.post(f"/api/projects/{created['id']}/rebuild", headers=AUTH)
    assert response.status_code == 202
    assert response.json() == {"status": "rebuilding"}
    wait_for(lambda: project(client, created["id"])["index"]["counts"]["indexed"] == 1)
    assert client.post("/api/projects/999/rebuild", headers=AUTH).status_code == 404


def test_delete_a_project_removes_its_chats_and_keeps_the_others(api):
    client, tmp_path = api
    docs = folder(tmp_path, "docs", a="Lease terms.")
    one = add_project(client, "One", docs)
    two = add_project(client, "Two", folder(tmp_path, "two"))
    first = client.post("/api/chats", headers=AUTH, json={"project_id": one["id"]}).json()["id"]
    client.post("/api/chats", headers=AUTH, json={"project_id": one["id"]})
    kept = client.post("/api/chats", headers=AUTH, json={"project_id": two["id"]}).json()["id"]
    deleted = client.delete(f"/api/projects/{one['id']}", headers=AUTH)
    assert deleted.status_code == 200
    assert deleted.json() == {"chats_deleted": 2}
    assert [p["id"] for p in client.get("/api/projects", headers=AUTH).json()["projects"]] == [
        two["id"]
    ]
    assert client.get(f"/api/chats/{first}", headers=AUTH).status_code == 404
    assert client.get(f"/api/chats/{kept}", headers=AUTH).status_code == 200
    assert (docs / "a.md").is_file()  # the user's folder is untouched
    assert client.delete(f"/api/projects/{one['id']}", headers=AUTH).status_code == 404


def test_deleting_a_project_is_refused_while_an_answer_streams(tmp_path):
    core, client = make_api(tmp_path, llm=FakeLocalLLM(FakeLLM(("one ", "two ", "three "))))
    try:
        created = add_project(client, "Docs", folder(tmp_path, "docs", a="The lease term is 3."))
        wait_for(lambda: project(client, created["id"])["index"]["counts"]["indexed"] == 1)
        chat = core.store.create_chat("Asking", created["id"])
        stream = core.answers.ask(chat.id, "How long is the lease term?")
        next(stream)
        next(stream)
        busy = client.delete(f"/api/projects/{created['id']}", headers=AUTH)
        assert busy.status_code == 409
        assert busy.json() == {"detail": "An answer is being written. Wait for it to finish."}
        assert project(client, created["id"]) is not None
        assert [e["type"] for e in stream][-1] == "done"
        assert client.delete(f"/api/projects/{created['id']}", headers=AUTH).status_code == 200
    finally:
        core.shutdown()


# --- chats: home and scope ---


def test_a_new_chat_has_the_first_project_as_home_and_scope(api):
    client, tmp_path = api
    one = add_project(client, "Alpha", folder(tmp_path, "one"))
    add_project(client, "Beta", folder(tmp_path, "two"))
    response = client.post("/api/chats", headers=AUTH)
    assert response.status_code == 201
    chat = response.json()
    assert set(chat) == {"id", "title", "created_at", "updated_at", "project_id", "scope"}
    assert (chat["project_id"], chat["scope"]) == (one["id"], [one["id"]])
    assert chat["title"] == ""


def test_a_new_chat_defaults_to_the_oldest_project_not_the_first_by_name(api):
    client, tmp_path = api
    oldest = add_project(client, "Zulu", folder(tmp_path, "one"))
    add_project(client, "Alpha", folder(tmp_path, "two"))
    chat = client.post("/api/chats", headers=AUTH).json()
    assert (chat["project_id"], chat["scope"]) == (oldest["id"], [oldest["id"]])


def test_moving_a_chat_to_a_project_deleted_meanwhile_is_a_404(tmp_path, monkeypatch):
    core, client = make_api(tmp_path)
    try:
        one = add_project(client, "Alpha", folder(tmp_path, "one"))
        two = add_project(client, "Beta", folder(tmp_path, "two"))
        chat_id = client.post("/api/chats", headers=AUTH).json()["id"]

        def gone(chat_id, collection_id):
            raise ValueError(f"unknown project {collection_id}")

        monkeypatch.setattr(core.store, "move_chat", gone)
        response = client.patch(
            f"/api/chats/{chat_id}", headers=AUTH, json={"project_id": two["id"]}
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Project not found."}
        assert client.get(f"/api/chats/{chat_id}", headers=AUTH).json()["project_id"] == one["id"]
    finally:
        core.shutdown()


def test_a_chat_can_be_created_in_a_chosen_project(api):
    client, tmp_path = api
    add_project(client, "Alpha", folder(tmp_path, "one"))
    two = add_project(client, "Beta", folder(tmp_path, "two"))
    created = client.post("/api/chats", headers=AUTH, json={"project_id": two["id"]})
    assert created.status_code == 201
    assert (created.json()["project_id"], created.json()["scope"]) == (two["id"], [two["id"]])
    assert client.post("/api/chats", headers=AUTH, json={"project_id": 999}).status_code == 404
    assert len(client.get("/api/chats", headers=AUTH).json()["chats"]) == 1


def test_a_chat_before_any_project_is_general_and_stays_so(api):
    client, tmp_path = api
    chat = client.post("/api/chats", headers=AUTH).json()
    assert (chat["project_id"], chat["scope"]) == (None, [])
    add_project(client, "Docs", folder(tmp_path, "docs"))
    kept = client.get(f"/api/chats/{chat['id']}", headers=AUTH).json()
    assert (kept["project_id"], kept["scope"]) == (None, [])


def test_a_general_chat_has_no_project_even_when_there_are_projects(api):
    client, tmp_path = api
    add_project(client, "Docs", folder(tmp_path, "docs"))
    chat = client.post("/api/chats", headers=AUTH, json={"general": True}).json()
    assert (chat["project_id"], chat["scope"]) == (None, [])
    both = client.post("/api/chats", headers=AUTH, json={"general": True, "project_id": 1})
    assert both.status_code == 400


def test_chats_list_and_detail_carry_home_and_scope(api):
    client, tmp_path = api
    one = add_project(client, "Alpha", folder(tmp_path, "one"))
    chat_id = client.post("/api/chats", headers=AUTH).json()["id"]
    listed = client.get("/api/chats", headers=AUTH).json()["chats"]
    assert [(c["project_id"], c["scope"]) for c in listed] == [(one["id"], [one["id"]])]
    detail = client.get(f"/api/chats/{chat_id}", headers=AUTH).json()
    assert (detail["project_id"], detail["scope"]) == (one["id"], [one["id"]])
    assert detail["messages"] == []


def test_set_the_search_scope(api):
    client, tmp_path = api
    one = add_project(client, "Alpha", folder(tmp_path, "one"))
    two = add_project(client, "Beta", folder(tmp_path, "two"))
    chat_id = client.post("/api/chats", headers=AUTH).json()["id"]
    both = client.put(
        f"/api/chats/{chat_id}/scope", headers=AUTH, json={"project_ids": [two["id"], one["id"]]}
    )
    assert both.status_code == 200
    assert both.json()["project_id"] == one["id"]  # the home does not change
    assert sorted(both.json()["scope"]) == sorted([one["id"], two["id"]])
    only_two = client.put(
        f"/api/chats/{chat_id}/scope", headers=AUTH, json={"project_ids": [two["id"]]}
    )
    assert only_two.json()["scope"] == [two["id"]]
    saved = client.get(f"/api/chats/{chat_id}", headers=AUTH).json()
    assert (saved["project_id"], saved["scope"]) == (one["id"], [two["id"]])


def test_scope_errors(api):
    client, tmp_path = api
    one = add_project(client, "Alpha", folder(tmp_path, "one"))
    chat_id = client.post("/api/chats", headers=AUTH).json()["id"]
    path = f"/api/chats/{chat_id}/scope"
    assert client.put(path, headers=AUTH, json={"project_ids": []}).status_code == 400
    assert client.put(path, headers=AUTH, json={"project_ids": [one["id"], 999]}).status_code == 400
    assert client.put(path, headers=AUTH, json={}).status_code == 422
    assert client.put(path, headers=AUTH, json={"project_ids": ["x"]}).status_code == 422
    gone = client.put("/api/chats/999/scope", headers=AUTH, json={"project_ids": [one["id"]]})
    assert gone.status_code == 404
    assert client.get(f"/api/chats/{chat_id}", headers=AUTH).json()["scope"] == [one["id"]]


def test_move_a_chat_to_another_home_project(api):
    client, tmp_path = api
    one = add_project(client, "Alpha", folder(tmp_path, "one"))
    two = add_project(client, "Beta", folder(tmp_path, "two"))
    chat_id = client.post("/api/chats", headers=AUTH).json()["id"]
    moved = client.patch(f"/api/chats/{chat_id}", headers=AUTH, json={"project_id": two["id"]})
    assert moved.status_code == 200
    assert (moved.json()["project_id"], moved.json()["scope"]) == (two["id"], [two["id"]])
    assert moved.json()["id"] == chat_id
    client.put(
        f"/api/chats/{chat_id}/scope", headers=AUTH, json={"project_ids": [one["id"], two["id"]]}
    )
    back = client.patch(f"/api/chats/{chat_id}", headers=AUTH, json={"project_id": one["id"]})
    assert back.json()["project_id"] == one["id"]
    assert sorted(back.json()["scope"]) == sorted([one["id"], two["id"]])  # the scope grows


def test_a_chat_can_be_renamed_and_moved_at_once(api):
    client, tmp_path = api
    add_project(client, "Alpha", folder(tmp_path, "one"))
    two = add_project(client, "Beta", folder(tmp_path, "two"))
    chat_id = client.post("/api/chats", headers=AUTH).json()["id"]
    changed = client.patch(
        f"/api/chats/{chat_id}", headers=AUTH, json={"title": " Lease ", "project_id": two["id"]}
    )
    assert changed.status_code == 200
    assert (changed.json()["title"], changed.json()["project_id"]) == ("Lease", two["id"])


def test_a_failed_chat_change_changes_nothing(api):
    client, tmp_path = api
    one = add_project(client, "Alpha", folder(tmp_path, "one"))
    two = add_project(client, "Beta", folder(tmp_path, "two"))
    chat_id = client.post("/api/chats", headers=AUTH).json()["id"]
    path = f"/api/chats/{chat_id}"
    blank = client.patch(path, headers=AUTH, json={"title": "   ", "project_id": two["id"]})
    assert blank.status_code == 400
    unknown = client.patch(path, headers=AUTH, json={"title": "Kept", "project_id": 999})
    assert unknown.status_code == 404
    assert (
        client.patch("/api/chats/999", headers=AUTH, json={"project_id": one["id"]}).status_code
        == 404
    )
    chat = client.get(path, headers=AUTH).json()
    assert (chat["title"], chat["project_id"]) == ("", one["id"])
    assert client.patch(path, headers=AUTH, json={}).json()["project_id"] == one["id"]


def test_deleting_a_project_removes_it_from_other_chats_scopes(api):
    client, tmp_path = api
    one = add_project(client, "Alpha", folder(tmp_path, "one"))
    two = add_project(client, "Beta", folder(tmp_path, "two"))
    chat_id = client.post("/api/chats", headers=AUTH, json={"project_id": one["id"]}).json()["id"]
    client.put(
        f"/api/chats/{chat_id}/scope", headers=AUTH, json={"project_ids": [one["id"], two["id"]]}
    )
    assert client.delete(f"/api/projects/{two['id']}", headers=AUTH).json() == {"chats_deleted": 0}
    assert client.get(f"/api/chats/{chat_id}", headers=AUTH).json()["scope"] == [one["id"]]


def test_the_old_collection_routes_are_gone(api):
    client, _ = api
    assert client.get("/api/collection", headers=AUTH).status_code == 404
    assert client.put("/api/collection", headers=AUTH, json={}).status_code in (404, 405)
    assert client.post("/api/collection/rebuild", headers=AUTH).status_code in (404, 405)


# --- the token ---


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/projects", None),
        ("POST", "/api/projects", {"name": "x", "folder_path": "C:/x"}),
        ("PATCH", "/api/projects/1", {"name": "x"}),
        ("PUT", "/api/projects/1/folder", {"folder_path": "C:/x"}),
        ("POST", "/api/projects/1/rebuild", None),
        ("DELETE", "/api/projects/1", None),
        ("POST", "/api/chats", {"project_id": 1}),
        ("PATCH", "/api/chats/1", {"project_id": 1}),
        ("PUT", "/api/chats/1/scope", {"project_ids": [1]}),
        ("GET", "/api/chats/1", None),
    ],
)
def test_every_project_and_chat_route_needs_the_token(api, method, path, body):
    client, _ = api
    assert client.request(method, path, json=body).status_code == 401
    assert (
        client.request(method, path, json=body, headers={"X-Tamra-Token": "wrong"}).status_code
        == 401
    )


# --- added files ---


def test_a_dropped_file_is_saved_in_the_project_folder_and_indexed(api):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    created = add_project(client, "Lease", docs)
    added = client.put(
        f"/api/projects/{created['id']}/files/สัญญาเช่า.md",
        headers=AUTH,
        content=b"The lease term is three years.",
    )
    assert added.status_code == 201, added.text
    assert added.json() == {"name": "สัญญาเช่า.md"}
    assert (docs / "สัญญาเช่า.md").read_text(encoding="utf-8") == "The lease term is three years."
    assert [p.name for p in docs.iterdir()] == ["สัญญาเช่า.md"]  # no temp file is left
    wait_for(lambda: project(client, created["id"])["index"]["counts"]["indexed"] == 1)


def test_a_file_with_a_name_already_in_the_folder_is_a_409_and_left_as_it_was(api):
    client, tmp_path = api
    docs = folder(tmp_path, "docs", lease="original")
    created = add_project(client, "Lease", docs)
    added = client.put(
        f"/api/projects/{created['id']}/files/lease.md", headers=AUTH, content=b"replacement"
    )
    assert added.status_code == 409
    assert (docs / "lease.md").read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize(
    "name",
    [
        "..%5Coutside.md",
        "C%3Aoutside.md",
        "notes.xlsx",
        "notes",
        ".hidden.md",
        "~$lock.docx",
        "CON.txt",
        "lpt1.md",
        "trailing.md.",
        "a%3Fb.md",
        "bell%07.md",
    ],
)
def test_a_name_that_is_not_a_plain_supported_file_name_is_a_400(api, name):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    created = add_project(client, "Lease", docs)
    added = client.put(f"/api/projects/{created['id']}/files/{name}", headers=AUTH, content=b"x")
    assert added.status_code == 400
    assert list(docs.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data", "docs"]


@pytest.mark.parametrize("name, status", [("..", 405), ("..%2Foutside.md", 404)])
def test_a_path_in_the_name_never_reaches_the_route(api, name, status):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    created = add_project(client, "Lease", docs)
    added = client.put(f"/api/projects/{created['id']}/files/{name}", headers=AUTH, content=b"x")
    assert added.status_code == status
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data", "docs"]


def test_a_file_for_an_unknown_project_is_a_404(api):
    client, _ = api
    assert client.put("/api/projects/99/files/a.md", headers=AUTH, content=b"x").status_code == 404


def test_adding_a_file_needs_the_token(api):
    client, tmp_path = api
    docs = folder(tmp_path, "docs")
    created = add_project(client, "Lease", docs)
    assert client.put(f"/api/projects/{created['id']}/files/a.md", content=b"x").status_code == 401
    assert list(docs.iterdir()) == []
