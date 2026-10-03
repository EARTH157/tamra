import pytest
from fakes import FakeEmbedder, FakeLLM

from tamra.answer import (
    AnswerService,
    AnswerSettings,
    detect_language,
    location_label,
)
from tamra.llm.llama_server import LlamaServerError
from tamra.llm.openai_compat import LLMError
from tamra.store import ChunkInput, Store


@pytest.fixture
def env(tmp_path):
    store = Store.open(tmp_path / "t.db")
    collection = store.replace_collection("Docs", "C:/docs", "m")
    embedder = FakeEmbedder()
    texts = ["The lease term is three years", "Parking costs fifty baht"]
    file_id = store.add_file(collection.id, "lease.md", 1, 1.0)
    chunks = [
        ChunkInput(t, {"kind": "text", "line_start": i + 1, "line_end": i + 1})
        for i, t in enumerate(texts)
    ]
    store.replace_file_chunks(file_id, chunks, embedder.embed(texts), content_hash="h", note=None)

    def make(llm):
        return AnswerService(
            store,
            lambda text: embedder.embed([text])[0],
            lambda: llm() if callable(llm) and not hasattr(llm, "generate") else llm,
            model_id="m",
            llm_label=lambda: "fake-model",
            settings=AnswerSettings(min_similarity=0.3),
        )

    yield store, make, store.create_chat()
    store.close()


def test_an_answer_streams_sources_then_tokens_and_is_saved(env):
    store, make, chat = env
    llm = FakeLLM()
    events = list(make(llm).ask(chat.id, "How long is the lease term?"))
    assert [e["type"] for e in events] == ["sources", "token", "token", "token", "done"]
    first = events[0]["sources"][0]
    assert (first["n"], first["file"], first["label"]) == (1, "lease.md", "line 1")
    assert first["text"] == "The lease term is three years"
    saved = store.list_messages(chat.id)
    assert [m.role for m in saved] == ["user", "assistant"]
    assert saved[1].content == "The lease is three years [1]."
    assert (saved[1].provider, saved[1].model) == ("local", "fake-model")
    assert [s.n for s in saved[1].sources] == [1, 2]
    assert events[-1]["message_id"] == saved[1].id
    system = llm.calls[0][0]["content"]
    assert "[1] lease.md (line 1)" in system
    assert "in English" in system


def test_an_unrelated_question_gets_not_found_without_the_llm(env):
    store, make, chat = env
    llm = FakeLLM()
    events = list(make(llm).ask(chat.id, "ใครเป็นผู้จัดการฝ่ายขาย"))
    assert [e["type"] for e in events] == ["sources", "token", "done"]
    assert events[1]["text"] == "ไม่พบข้อมูลนี้ในเอกสาร"
    assert llm.calls == []
    assert store.list_messages(chat.id)[1].content == "ไม่พบข้อมูลนี้ในเอกสาร"


def test_follow_ups_carry_the_previous_exchange(env):
    store, make, chat = env
    llm = FakeLLM()
    service = make(llm)
    list(service.ask(chat.id, "How long is the lease term?"))
    list(service.ask(chat.id, "and parking?"))
    second = llm.calls[1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == "How long is the lease term?"
    assert second[3]["content"] == "and parking?"


def test_cancel_stops_generation_and_keeps_the_partial_answer(env):
    store, make, chat = env
    service = make(FakeLLM(("one ", "two ", "three ")))
    stream = service.ask(chat.id, "How long is the lease term?")
    assert next(stream)["type"] == "sources"
    assert next(stream) == {"type": "token", "text": "one "}
    service.cancel()
    assert [e["type"] for e in stream] == ["done"]
    assert store.list_messages(chat.id)[1].content == "one "


def test_closing_the_stream_saves_the_partial_answer_and_frees_the_service(env):
    store, make, chat = env
    service = make(FakeLLM())
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)
    next(stream)
    stream.close()
    assert store.list_messages(chat.id)[1].content == "The lease is three years "
    assert [e["type"] for e in service.ask(chat.id, "lease term?")][-1] == "done"


def test_a_second_question_while_answering_is_refused(env):
    store, make, chat = env
    service = make(FakeLLM())
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)
    assert list(service.ask(chat.id, "lease?")) == [
        {"type": "error", "message": "Another answer is still being written."}
    ]
    list(stream)


def test_llm_errors_are_reported_and_partial_text_kept(env):
    store, make, chat = env

    class Broken:
        def generate(self, messages, max_tokens=1024):
            yield "partial "
            raise LLMError("HTTP 500: boom")

    events = list(make(Broken()).ask(chat.id, "How long is the lease term?"))
    assert [e["type"] for e in events][-2:] == ["error", "done"]
    assert events[-2]["message"] == "HTTP 500: boom"
    assert store.list_messages(chat.id)[1].content == "partial "


def test_a_server_that_cannot_start_is_reported(env):
    store, make, chat = env

    def broken():
        raise LlamaServerError("Local model not found: m.gguf")

    events = list(make(broken).ask(chat.id, "How long is the lease term?"))
    assert events[-1] == {"type": "error", "message": "Local model not found: m.gguf"}
    assert [m.role for m in store.list_messages(chat.id)] == ["user"]


def test_problems_before_answering_are_single_error_events(tmp_path):
    store = Store.open(tmp_path / "t.db")
    service = AnswerService(
        store, lambda t: None, lambda: FakeLLM(), model_id="m", llm_label=lambda: "x"
    )
    chat = store.create_chat()
    assert list(service.ask(chat.id, "q")) == [
        {"type": "error", "message": "Choose a folder of documents first."}
    ]
    store.replace_collection("Docs", "C:/docs", "other-model")
    assert list(service.ask(chat.id, "q"))[0]["message"].startswith("The index was built with")
    store.replace_collection("Docs", "C:/docs", "m")
    assert list(service.ask(999, "q")) == [{"type": "error", "message": "Chat 999 not found."}]
    store.close()


def test_detect_language():
    assert detect_language("ลาพักร้อนได้กี่วัน") == "th"
    assert detect_language("沙发保修几年？") == "zh"
    assert detect_language("How many days?") == "en"
    assert detect_language("1234 ?") == "en"
    assert detect_language("Tamra ตอบคำถามจากเอกสาร") == "th"


def test_location_labels():
    pdf = {"kind": "pdf", "page_start": 3, "page_end": 3, "char_start": 0, "char_end": 5}
    assert location_label(pdf) == "p. 3"
    assert location_label({**pdf, "page_end": 4}) == "pp. 3-4"
    docx = {
        "kind": "docx",
        "heading_path": ["Leave", "Sick"],
        "paragraph_start": 4,
        "paragraph_end": 6,
    }
    assert location_label(docx) == "Leave > Sick, paras. 5-7"
    assert location_label({**docx, "heading_path": [], "paragraph_end": 4}) == "para. 5"
    assert location_label({"kind": "text", "line_start": 7, "line_end": 7}) == "line 7"
    assert location_label({"kind": "text", "line_start": 7, "line_end": 9}) == "lines 7-9"
