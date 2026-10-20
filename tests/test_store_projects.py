import sqlite3

import numpy as np
import pytest

from tamra.store import ChunkInput, Store, connect
from tamra.store.schema import _V1, _V2

MODEL = "test-model"
NOW = "2026-01-01T00:00:00+00:00"


def unit(*hot: int) -> np.ndarray:
    vector = np.zeros(1024, dtype=np.float32)
    vector[list(hot)] = 1.0
    return vector / np.linalg.norm(vector)


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "tamra.db")
    yield s
    s.close()


def add_indexed(store, collection_id, rel_path, text, vector):
    file_id = store.add_file(collection_id, rel_path, 10, 1.0)
    store.replace_file_chunks(
        file_id,
        [ChunkInput(text, {"kind": "text", "line_start": 1, "line_end": 1})],
        np.stack([vector]),
        content_hash="h",
        note=None,
    )
    return file_id


def vector_count(store, collection_id=None):
    sql, args = "SELECT count(*) FROM chunk_vectors", ()
    if collection_id is not None:
        sql, args = sql + " WHERE collection_id = ?", (collection_id,)
    return store._conn.execute(sql, args).fetchone()[0]


# Migration ------------------------------------------------------------------------------------


def test_a_v2_database_upgrades_to_v3_giving_every_chat_the_one_project(tmp_path):
    path = tmp_path / "tamra.db"
    conn = connect(path)
    conn.executescript("BEGIN;\n" + _V1 + _V2 + "\nPRAGMA user_version = 2;\nCOMMIT;")
    conn.execute(
        "INSERT INTO collections (id, name, folder_path, embedding_model_id, created_at)"
        f" VALUES (4, 'Docs', 'C:/docs', 'm', '{NOW}')"
    )
    conn.execute(
        "INSERT INTO files (id, collection_id, rel_path, size, mtime, status)"
        " VALUES (1, 4, 'a.md', 1, 1.0, 'pending')"
    )
    for chat_id, title in ((7, "First"), (8, "Second")):
        conn.execute(
            "INSERT INTO chats (id, title, created_at, updated_at, activity)"
            f" VALUES ({chat_id}, '{title}', '{NOW}', '{NOW}', {chat_id})"
        )
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, created_at)"
            f" VALUES ({chat_id}, 'user', 'hello {chat_id}', '{NOW}')"
        )
    conn.commit()
    conn.close()

    store = Store.open(path)
    try:
        assert [(c.id, c.name) for c in store.list_collections()] == [(4, "Docs")]
        assert [(c.id, c.collection_id, c.scope) for c in store.list_chats()] == [
            (8, 4, [4]),
            (7, 4, [4]),
        ]
        assert [m.content for m in store.list_messages(7)] == ["hello 7"]
        assert [m.content for m in store.list_messages(8)] == ["hello 8"]
        assert [f.rel_path for f in store.list_files(4)] == ["a.md"]
        assert store.list_files(4)[0].collection_id == 4
        with pytest.raises(ValueError, match="name taken"):
            store.create_collection("docs", "D:/docs", MODEL)
    finally:
        store.close()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    conn.close()


def test_a_v2_database_without_a_project_upgrades_with_homeless_chats(tmp_path):
    path = tmp_path / "tamra.db"
    conn = connect(path)
    conn.executescript("BEGIN;\n" + _V1 + _V2 + "\nPRAGMA user_version = 2;\nCOMMIT;")
    conn.execute(
        "INSERT INTO chats (id, title, created_at, updated_at, activity)"
        f" VALUES (1, 'Old', '{NOW}', '{NOW}', 1)"
    )
    conn.commit()
    conn.close()
    store = Store.open(path)
    try:
        chat = store.get_chat(1)
        assert (chat.collection_id, chat.scope) == (None, [])
    finally:
        store.close()


def test_the_v3_migration_renames_duplicate_names_before_the_unique_index(tmp_path):
    path = tmp_path / "tamra.db"
    conn = connect(path)
    conn.executescript("BEGIN;\n" + _V1 + _V2 + "\nPRAGMA user_version = 2;\nCOMMIT;")
    for collection_id, name in ((1, "Docs"), (2, "docs"), (3, "Other"), (4, "DOCS")):
        conn.execute(
            "INSERT INTO collections (id, name, folder_path, embedding_model_id, created_at)"
            f" VALUES ({collection_id}, '{name}', 'C:/{collection_id}', 'm', '{NOW}')"
        )
    conn.commit()
    conn.close()
    store = Store.open(path)
    try:
        names = {c.id: c.name for c in store.list_collections()}
        assert names == {1: "Docs", 2: "docs (2)", 3: "Other", 4: "DOCS (4)"}
        with pytest.raises(ValueError, match="name taken"):
            store.create_collection("OTHER", "D:/x", MODEL)
    finally:
        store.close()


# Collections ----------------------------------------------------------------------------------


def test_collections_are_listed_by_name_ignoring_case(store):
    store.create_collection("beta", "D:/b", MODEL)
    store.create_collection("Alpha", "D:/a", MODEL)
    store.create_collection("Gamma", "D:/g", MODEL)
    assert [c.name for c in store.list_collections()] == ["Alpha", "beta", "Gamma"]
    created = store.create_collection("Delta", "D:/d", MODEL)
    assert store.get_collection(created.id) == created
    assert store.get_collection(999) is None


def test_a_project_name_must_be_unique_ignoring_case(store):
    first = store.create_collection("Docs", "D:/a", MODEL)
    with pytest.raises(ValueError, match="name taken"):
        store.create_collection("DOCS", "D:/b", MODEL)
    second = store.create_collection("Notes", "D:/n", MODEL)
    with pytest.raises(ValueError, match="name taken"):
        store.rename_collection(second.id, "docs")
    assert store.rename_collection(second.id, "Papers") is True
    assert store.get_collection(second.id).name == "Papers"
    assert store.rename_collection(first.id, "DOCS") is True  # its own name, new case
    assert store.get_collection(first.id).name == "DOCS"
    assert store.rename_collection(999, "Whatever") is False


def test_set_collection_folder_drops_the_projects_whole_index(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    add_indexed(store, one.id, "a.md", "alpha beta", unit(1))
    add_indexed(store, two.id, "b.md", "gamma delta", unit(2))
    chat = store.create_chat("Kept", one.id)

    updated = store.set_collection_folder(one.id, "D:/elsewhere", "model-2")

    assert (updated.id, updated.name, updated.folder_path, updated.embedding_model_id) == (
        one.id,
        "One",
        "D:/elsewhere",
        "model-2",
    )
    assert store.list_files(one.id) == []
    assert store.search_keyword([one.id], '"alp"', 10) == []
    assert store.search_dense([one.id], unit(1), 10) == []
    assert vector_count(store, one.id) == 0
    assert len(store.list_files(two.id)) == 1  # another project is untouched
    assert vector_count(store, two.id) == 1
    assert store.get_chat(chat.id).scope == [one.id]
    assert store.set_collection_folder(999, "D:/x", MODEL) is None


def test_delete_collection_removes_its_index_and_home_chats(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    add_indexed(store, one.id, "a.md", "alpha beta", unit(1))
    add_indexed(store, two.id, "b.md", "gamma delta", unit(2))
    home = store.create_chat("Home of one", one.id)
    store.add_user_message(home.id, "question")
    other = store.create_chat("Home of two", two.id)

    assert store.delete_collection(one.id) == 1

    assert [c.id for c in store.list_collections()] == [two.id]
    assert store.get_chat(home.id) is None
    assert store.list_messages(home.id) == []
    assert store.get_chat(other.id).scope == [two.id]
    assert store.list_files(one.id) == []
    assert vector_count(store, one.id) == 0
    assert vector_count(store) == 1
    assert store.search_keyword([two.id], '"gam"', 10)
    assert store._conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1
    assert store.delete_collection(999) is None
    assert store.delete_collection(two.id) == 1  # a project with one home chat


def test_a_chat_that_also_searched_a_deleted_project_loses_it_from_its_scope(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    chat = store.create_chat("Both", two.id)
    store.set_chat_scope(chat.id, [one.id, two.id])
    assert store.delete_collection(one.id) == 0
    assert store.get_chat(chat.id).scope == [two.id]


def test_a_chat_left_with_an_empty_scope_falls_back_to_its_home(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    chat = store.create_chat("Home two, searching one", two.id)
    store.set_chat_scope(chat.id, [one.id])
    assert store.delete_collection(one.id) == 0
    found = store.get_chat(chat.id)
    assert (found.collection_id, found.scope) == (two.id, [two.id])


# Chats ----------------------------------------------------------------------------------------


def test_homeless_chats_are_adopted_by_a_project(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    homed = store.create_chat("Homed", two.id)
    first = store.create_chat("First")
    second = store.create_chat("Second")
    assert store.adopt_homeless_chats(one.id) == 2
    for chat_id in (first.id, second.id):
        adopted = store.get_chat(chat_id)
        assert (adopted.collection_id, adopted.scope) == (one.id, [one.id])
    assert (store.get_chat(homed.id).collection_id, store.get_chat(homed.id).scope) == (
        two.id,
        [two.id],
    )
    assert store.adopt_homeless_chats(one.id) == 0
    with pytest.raises(ValueError):
        store.adopt_homeless_chats(999)


def test_a_chats_scope_is_read_for_that_chat_only(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    mine = store.create_chat("Mine", one.id)
    store.set_chat_scope(mine.id, [one.id, two.id])
    other = store.create_chat("Other", two.id)
    assert store.get_chat(mine.id).scope == [one.id, two.id]
    assert store.get_chat(other.id).scope == [two.id]
    assert {c.id: c.scope for c in store.list_chats()} == {
        mine.id: [one.id, two.id],
        other.id: [two.id],
    }


def test_a_new_chat_searches_its_home_project(store):
    one = store.create_collection("One", "D:/one", MODEL)
    homed = store.create_chat("Homed", one.id)
    homeless = store.create_chat()
    assert (homed.collection_id, homed.scope) == (one.id, [one.id])
    assert (homeless.collection_id, homeless.scope) == (None, [])
    assert store.get_chat(homed.id) == homed
    assert store.list_chats() == [homeless, homed]
    with pytest.raises(ValueError):
        store.create_chat("Nowhere", 999)
    assert len(store.list_chats()) == 2


def test_moving_a_chat_whose_scope_was_its_home_moves_the_scope_too(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    chat = store.create_chat("Move me", one.id)
    assert store.move_chat(chat.id, two.id) is True
    moved = store.get_chat(chat.id)
    assert (moved.collection_id, moved.scope) == (two.id, [two.id])


def test_moving_a_chat_that_searches_more_adds_the_new_home_to_its_scope(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    three = store.create_collection("Three", "D:/three", MODEL)
    chat = store.create_chat("Wide", one.id)
    store.set_chat_scope(chat.id, [one.id, two.id])
    assert store.move_chat(chat.id, three.id) is True
    moved = store.get_chat(chat.id)
    assert moved.collection_id == three.id
    assert sorted(moved.scope) == sorted([one.id, two.id, three.id])


def test_moving_a_homeless_chat_gives_it_the_project_as_home_and_scope(store):
    one = store.create_collection("One", "D:/one", MODEL)
    chat = store.create_chat()
    assert store.move_chat(chat.id, one.id) is True
    moved = store.get_chat(chat.id)
    assert (moved.collection_id, moved.scope) == (one.id, [one.id])


def test_move_chat_rejects_unknown_chats_and_projects(store):
    one = store.create_collection("One", "D:/one", MODEL)
    chat = store.create_chat("Stay", one.id)
    assert store.move_chat(999, one.id) is False
    with pytest.raises(ValueError):
        store.move_chat(chat.id, 999)
    assert store.get_chat(chat.id).collection_id == one.id


def test_set_chat_scope_validates_its_input(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    chat = store.create_chat("Scoped", one.id)
    assert store.set_chat_scope(chat.id, [two.id, one.id, two.id]) is True
    assert store.get_chat(chat.id).scope == sorted([one.id, two.id])
    with pytest.raises(ValueError):
        store.set_chat_scope(chat.id, [])
    with pytest.raises(ValueError):
        store.set_chat_scope(chat.id, [one.id, 999])
    assert store.get_chat(chat.id).scope == sorted([one.id, two.id])  # rejected: unchanged
    assert store.set_chat_scope(999, [one.id]) is False


def test_deleting_a_chat_drops_its_scope_rows(store):
    one = store.create_collection("One", "D:/one", MODEL)
    chat = store.create_chat("Gone", one.id)
    assert store.delete_chat(chat.id) is True
    assert store._conn.execute("SELECT count(*) FROM chat_collections").fetchone()[0] == 0


# Search ---------------------------------------------------------------------------------------


@pytest.fixture
def two_projects(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    add_indexed(store, one.id, "a.md", "lease alpha", unit(1))
    add_indexed(store, two.id, "b.md", "lease beta", unit(1, 2))
    return one, two


def owner(store, chunk_id):
    return store.get_file(store.get_chunks([chunk_id])[0].file_id).collection_id


def test_dense_search_covers_every_scoped_project(store, two_projects):
    one, two = two_projects
    both = store.search_dense([one.id, two.id], unit(1), 10)
    assert {owner(store, chunk_id) for chunk_id, _ in both} == {one.id, two.id}
    similarities = [similarity for _, similarity in both]
    assert similarities == sorted(similarities, reverse=True)
    assert {owner(store, c) for c, _ in store.search_dense([one.id], unit(1), 10)} == {one.id}
    assert {owner(store, c) for c, _ in store.search_dense([two.id], unit(1), 10)} == {two.id}
    assert store.search_dense([], unit(1), 10) == []


def test_dense_search_keeps_only_the_best_k_of_the_merge(store, two_projects):
    one, two = two_projects
    best = store.search_dense([one.id, two.id], unit(1), 1)
    assert len(best) == 1
    assert owner(store, best[0][0]) == one.id  # an exact match beats a partial one


def test_keyword_search_covers_every_scoped_project(store, two_projects):
    one, two = two_projects
    both = store.search_keyword([one.id, two.id], '"lea"', 10)
    assert {owner(store, chunk_id) for chunk_id in both} == {one.id, two.id}
    assert {owner(store, c) for c in store.search_keyword([one.id], '"lea"', 10)} == {one.id}
    assert store.search_keyword([], '"lea"', 10) == []
    assert len(store.search_keyword([one.id, two.id], '"lea"', 1)) == 1


def test_keyword_search_breaks_score_ties_by_chunk_id(store):
    project = store.create_collection("One", "D:/one", MODEL)
    for name in ("a.md", "b.md", "c.md"):
        add_indexed(store, project.id, name, "identical lease text", unit(1))
    found = store.search_keyword([project.id], '"lea"', 10)
    assert found == sorted(found) and len(found) == 3
    assert store.search_keyword([project.id], '"lea"', 2) == found[:2]


# Queue ----------------------------------------------------------------------------------------


def test_next_pending_file_any_is_the_oldest_across_projects(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    first = store.add_file(two.id, "z.md", 1, 1.0)
    second = store.add_file(one.id, "a.md", 1, 1.0)
    found = store.next_pending_file_any(MODEL)
    assert (found.id, found.collection_id) == (first, two.id)
    store.set_file_status(first, "indexing")
    assert store.next_pending_file_any(MODEL).id == second
    store.set_file_status(second, "indexing")
    assert store.next_pending_file_any(MODEL) is None


def test_next_pending_file_any_skips_a_stale_project(store):
    stale = store.create_collection("Stale", "D:/stale", "old-model")
    fresh = store.create_collection("Fresh", "D:/fresh", MODEL)
    store.add_file(stale.id, "a.md", 1, 1.0)
    waiting = store.add_file(fresh.id, "b.md", 1, 1.0)
    assert store.next_pending_file_any(MODEL).id == waiting
    store.set_file_status(waiting, "indexing")
    assert store.next_pending_file_any(MODEL) is None


def test_next_pending_file_any_skips_excluded_projects(store):
    one = store.create_collection("One", "D:/one", MODEL)
    two = store.create_collection("Two", "D:/two", MODEL)
    store.add_file(one.id, "a.md", 1, 1.0)
    waiting = store.add_file(two.id, "b.md", 1, 1.0)
    assert store.next_pending_file_any(MODEL, [one.id]).id == waiting
    assert store.next_pending_file_any(MODEL, [one.id, two.id]) is None
