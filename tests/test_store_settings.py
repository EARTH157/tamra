import sqlite3

import pytest

from tamra.store import Store, connect
from tamra.store.schema import _V1, SCHEMA_VERSION


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "tamra.db")
    yield s
    s.close()


def test_fresh_database_is_created_at_schema_v2_with_a_settings_table(tmp_path):
    path = tmp_path / "tamra.db"
    Store.open(path).close()
    conn = sqlite3.connect(path)
    assert SCHEMA_VERSION == 2
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    columns = {row[1] for row in conn.execute("PRAGMA table_info(settings)")}
    assert columns == {"key", "value_json"}
    conn.close()


def test_a_v1_database_upgrades_in_place_and_keeps_its_data(tmp_path):
    path = tmp_path / "tamra.db"
    conn = connect(path)
    conn.executescript("BEGIN;\n" + _V1 + "\nPRAGMA user_version = 1;\nCOMMIT;")
    conn.execute(
        "INSERT INTO chats (id, title, created_at, updated_at, activity)"
        " VALUES (7, 'Old chat', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 1)"
    )
    conn.execute(
        "INSERT INTO messages (chat_id, role, content, created_at)"
        " VALUES (7, 'user', 'hello', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    store = Store.open(path)
    chat = store.get_chat(7)
    assert chat is not None and chat.title == "Old chat"
    assert [m.content for m in store.list_messages(7)] == ["hello"]
    assert store.all_settings() == {}
    store.set_settings({"mode": "api"})
    assert store.get_setting("mode") == "api"
    store.close()

    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.close()


def test_a_failed_upgrade_leaves_a_v1_database_untouched(tmp_path):
    path = tmp_path / "tamra.db"
    conn = connect(path)
    conn.executescript("BEGIN;\n" + _V1 + "\nPRAGMA user_version = 1;\nCOMMIT;")
    conn.execute("CREATE TABLE settings (blocker TEXT)")  # makes the v2 step fail
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.OperationalError):
        Store.open(path)

    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_settings_start_empty(store):
    assert store.all_settings() == {}
    assert store.get_setting("mode") is None


def test_set_settings_stores_json_values_and_updates_existing_keys(store):
    store.set_settings({"mode": "api", "ask_before_delete": False, "api_model": "ไทย"})
    assert store.get_setting("mode") == "api"
    assert store.get_setting("ask_before_delete") is False
    assert store.all_settings() == {"mode": "api", "ask_before_delete": False, "api_model": "ไทย"}
    store.set_settings({"mode": "local", "local_model_id": None})
    assert store.get_setting("mode") == "local"
    assert store.get_setting("local_model_id") is None
    assert "local_model_id" in store.all_settings()  # a stored null is not a missing key


def test_set_settings_is_one_transaction(store):
    with pytest.raises(TypeError):
        store.set_settings({"mode": "api", "bad": object()})  # not JSON serializable
    assert store.all_settings() == {}


def test_settings_persist_across_reopen(tmp_path):
    path = tmp_path / "tamra.db"
    first = Store.open(path)
    first.set_settings({"theme": "dark"})
    first.close()
    second = Store.open(path)
    assert second.get_setting("theme") == "dark"
    second.close()
