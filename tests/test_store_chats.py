import numpy as np
import pytest

from tamra.store import ChunkInput, SourceRecord, Store


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "tamra.db")
    yield s
    s.close()


def source(n, text="The lease is three years.", rel_path="a.md"):
    return SourceRecord(
        n=n,
        chunk_id=None,
        file_id=None,
        rel_path=rel_path,
        text=text,
        location={"kind": "text", "line_start": n, "line_end": n},
        file_hash="h",
    )


def test_chats_are_listed_most_recent_first(store):
    first = store.create_chat()
    second = store.create_chat("Named")
    assert [c.id for c in store.list_chats()] == [second.id, first.id]
    store.add_user_message(first.id, "bump")
    assert store.list_chats()[0].id == first.id
    assert store.get_chat(second.id).title == "Named"
    assert store.get_chat(999) is None


def test_first_question_becomes_the_title(store):
    chat = store.create_chat()
    store.add_user_message(chat.id, "  What   is the\nlease term " + "x" * 80)
    store.add_user_message(chat.id, "second question")
    title = store.get_chat(chat.id).title
    assert title.startswith("What is the lease term ")
    assert len(title) == 60


def test_assistant_messages_keep_their_sources(store):
    chat = store.create_chat()
    store.add_user_message(chat.id, "How long is the lease?")
    message_id = store.add_assistant_message(
        chat.id, "Three years [1].", provider="local", model="qwen", sources=[source(1), source(2)]
    )
    messages = store.list_messages(chat.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    answer = messages[1]
    assert (answer.id, answer.content, answer.provider, answer.model) == (
        message_id,
        "Three years [1].",
        "local",
        "qwen",
    )
    assert [s.n for s in answer.sources] == [1, 2]
    assert answer.sources[0].location == {"kind": "text", "line_start": 1, "line_end": 1}
    assert messages[0].sources == ()


def test_last_exchange_returns_the_latest_question_and_its_answer(store):
    chat = store.create_chat()
    assert store.last_exchange(chat.id) == (None, None)
    store.add_user_message(chat.id, "q1")
    store.add_assistant_message(chat.id, "a1", provider=None, model=None, sources=[])
    store.add_user_message(chat.id, "q2")
    assert store.last_exchange(chat.id) == ("q2", None)
    store.add_assistant_message(chat.id, "a2", provider=None, model=None, sources=[])
    assert store.last_exchange(chat.id) == ("q2", "a2")


def test_deleting_a_chat_removes_its_messages(store):
    chat = store.create_chat()
    store.add_user_message(chat.id, "q")
    store.add_assistant_message(chat.id, "a", provider=None, model=None, sources=[source(1)])
    assert store.delete_chat(chat.id) is True
    assert store.delete_chat(chat.id) is False
    assert store.list_messages(chat.id) == []


def test_message_sources_outlive_the_indexed_file(store):
    coll = store.replace_collection("Docs", "C:/docs", "m")
    file_id = store.add_file(coll.id, "a.md", 1, 1.0)
    vector = np.zeros(1024, dtype=np.float32)
    vector[0] = 1.0
    store.replace_file_chunks(
        file_id,
        [ChunkInput("lease text", {"kind": "text", "line_start": 1, "line_end": 1})],
        vector[None, :],
        content_hash="h",
        note=None,
    )
    chunk = store.get_chunks([store.search_dense(coll.id, vector, 1)[0][0]])[0]
    chat = store.create_chat()
    store.add_user_message(chat.id, "q")
    store.add_assistant_message(
        chat.id,
        "a [1]",
        provider="local",
        model="m",
        sources=[SourceRecord(1, chunk.id, file_id, "a.md", chunk.text, chunk.location, "h")],
    )
    store.delete_file(file_id)
    kept = store.list_messages(chat.id)[1].sources[0]
    assert (kept.rel_path, kept.text, kept.chunk_id) == ("a.md", "lease text", chunk.id)


def test_renaming_a_chat_normalizes_the_title(store):
    first = store.create_chat("First")
    second = store.create_chat("Second")
    assert store.rename_chat(first.id, "  Lease \n  terms\tsummary  ") is True
    assert store.get_chat(first.id).title == "Lease terms summary"
    assert store.rename_chat(first.id, "y" * 80) is True
    assert store.get_chat(first.id).title == "y" * 60
    assert [c.id for c in store.list_chats()] == [second.id, first.id]  # order is kept


def test_a_renamed_title_cut_on_a_space_has_no_trailing_space(store):
    chat = store.create_chat()
    title = "a" * 59 + " tail"  # character 60 is the space
    assert store.rename_chat(chat.id, title) is True
    assert store.get_chat(chat.id).title == "a" * 59


def test_renaming_rejects_an_empty_title_and_unknown_chats(store):
    chat = store.create_chat("Kept")
    with pytest.raises(ValueError):
        store.rename_chat(chat.id, " \n\t ")
    assert store.get_chat(chat.id).title == "Kept"
    assert store.rename_chat(999, "Anything") is False
