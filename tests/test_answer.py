import numpy as np
import pytest
from fakes import FakeEmbedder, FakeLLM

from tamra.answer import (
    NOT_FOUND,
    AnswerService,
    AnswerSettings,
    NothingIndexed,
    Route,
    StaleIndex,
    auto_citation,
    build_messages,
    chat_messages,
    cited_numbers,
    detect_language,
    estimate_tokens,
    location_label,
    source_payloads,
)
from tamra.embedder import EmbedderUnavailable
from tamra.llm.base import THINKING_HEADROOM, Chunk, LLMError, ProviderError, Usage
from tamra.llm.llama_server import LlamaServerError
from tamra.store import ChunkInput, SourceRecord, Store


@pytest.fixture
def env(tmp_path):
    store = Store.open(tmp_path / "t.db")
    collection = store.create_collection("Docs", "C:/docs", "m")
    embedder = FakeEmbedder()
    texts = ["The lease term is three years", "Parking costs fifty baht"]
    file_id = store.add_file(collection.id, "lease.md", 1, 1.0)
    chunks = [
        ChunkInput(t, {"kind": "text", "line_start": i + 1, "line_end": i + 1})
        for i, t in enumerate(texts)
    ]
    store.replace_file_chunks(file_id, chunks, embedder.embed(texts), content_hash="h", note=None)

    def make(llm, name="local", settings=None, rerank=None, context_tokens=8192):
        def opened():
            return llm() if callable(llm) and not hasattr(llm, "generate") else llm

        return AnswerService(
            store,
            lambda text: embedder.embed([text])[0],
            lambda: Route(name, opened, context_tokens),
            model_id="m",
            settings=settings or AnswerSettings(min_similarity=0.3),
            rerank=rerank,
        )

    yield store, make, store.create_chat(collection_id=collection.id)
    store.close()


def _source_texts(events):
    return [s["text"] for s in next(e for e in events if e["type"] == "sources")["sources"]]


def test_the_reranker_reorders_the_fused_candidates(env):
    _, make, chat = env
    calls = []

    def rerank(query, texts):
        calls.append((query, texts))
        return np.array([1.0 if "Parking" in t else 0.0 for t in texts])

    events = list(make(FakeLLM(), rerank=rerank).ask(chat.id, "How long is the lease term?"))
    assert _source_texts(events) == ["Parking costs fifty baht", "The lease term is three years"]
    assert calls[0][0] == "How long is the lease term?"
    assert sorted(calls[0][1]) == ["Parking costs fifty baht", "The lease term is three years"]


def test_the_reranker_sees_at_most_rerank_candidates_and_the_top_k_are_kept(env):
    _, make, chat = env
    seen = []

    def rerank(query, texts):
        seen.append(len(texts))
        return np.array([1.0 if "Parking" in t else 0.0 for t in texts])

    settings = AnswerSettings(min_similarity=0.3, top_k=1, rerank_candidates=1)
    events = list(make(FakeLLM(), settings=settings, rerank=rerank).ask(chat.id, "lease term?"))
    assert seen == [1]  # max(top_k, rerank_candidates): only the best fused hit is scored
    assert _source_texts(events) == ["The lease term is three years"]


@pytest.mark.parametrize("failure", ["missing", "raises"])
def test_answers_keep_the_fused_order_without_a_working_reranker(env, failure):
    _, make, chat = env

    def rerank(query, texts):
        if failure == "raises":
            raise RuntimeError("onnxruntime fell over")
        return None  # not installed

    events = list(make(FakeLLM(), rerank=rerank).ask(chat.id, "How long is the lease term?"))
    assert _source_texts(events)[0] == "The lease term is three years"
    assert events[-1]["type"] == "done"


def test_the_not_found_gate_stays_on_the_dense_similarity(env):
    _, make, chat = env
    calls = []

    def rerank(query, texts):
        calls.append(texts)
        return np.ones(len(texts))  # a reranker that likes everything changes nothing here

    events = list(make(FakeLLM(), rerank=rerank).ask(chat.id, "ใครเป็นผู้จัดการฝ่ายขาย"))
    assert events[1]["text"] == "ไม่พบข้อมูลนี้ในเอกสาร"
    assert calls == []


def test_an_answer_streams_sources_then_tokens_and_is_saved(env):
    store, make, chat = env
    llm = FakeLLM()
    events = list(make(llm).ask(chat.id, "How long is the lease term?"))
    assert [e["type"] for e in events] == ["sources", "context", "token", "token", "token", "done"]
    first = events[0]["sources"][0]
    assert (first["n"], first["file"], first["label"]) == (1, "lease.md", "line 1")
    assert first["text"] == "The lease term is three years"
    saved = store.list_messages(chat.id)
    assert [m.role for m in saved] == ["user", "assistant"]
    assert saved[1].content == "The lease is three years [1]."
    assert (saved[1].provider, saved[1].model) == ("local", "fake-llm")
    assert [s.n for s in saved[1].sources] == [1, 2]
    assert events[-1]["message_id"] == saved[1].id
    system = llm.calls[0][0]["content"]
    assert "[1] lease.md (line 1)" in system
    assert "in English" in system
    assert system.endswith("Example answer format: The rent is 10,000 baht [1].")


def test_an_unrelated_question_gets_not_found_without_the_llm(env):
    store, make, chat = env
    llm = FakeLLM()
    events = list(make(llm).ask(chat.id, "ใครเป็นผู้จัดการฝ่ายขาย"))
    assert [e["type"] for e in events] == ["sources", "token", "done"]  # no model: no context
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


def test_the_model_sees_the_earlier_exchanges_that_fit_oldest_first(env):
    store, make, chat = env
    llm = FakeLLM()
    service = make(llm)
    for i in range(5):
        list(service.ask(chat.id, f"How long is the lease term? {i}"))
    list(service.ask(chat.id, "and parking?"))
    last = llm.calls[-1]
    assert [m["role"] for m in last] == ["system"] + ["user", "assistant"] * 5 + ["user"]
    assert [m["content"] for m in last[1:-1:2]] == [
        f"How long is the lease term? {i}" for i in range(5)
    ]
    assert last[2]["content"] == "The lease is three years."  # its [1] pointed at old sources
    assert last[-1]["content"] == "and parking?"


def _record(n: int) -> SourceRecord:
    return SourceRecord(
        n,
        n,
        n,
        "lease.md",
        "The lease term is three years",
        {"kind": "text", "line_start": 1, "line_end": 1},
        "h",
    )


def test_build_messages_adds_answered_exchanges_without_their_markers():
    history = [
        ("q1", "The lease is three years [1]. Parking is free [2][3]."),
        ("q2", None),  # cancelled or failed
        ("q3", ""),  # search mode: passages only
        ("q3b", " [1]"),  # nothing left once the marker goes
        ("q4", "Rent is due [1, 2] monthly."),
    ]
    messages = build_messages("q5", history, [_record(1)], "en")
    assert [(m["role"], m["content"]) for m in messages[1:]] == [
        ("user", "q1"),
        ("assistant", "The lease is three years. Parking is free."),
        ("user", "q4"),
        ("assistant", "Rent is due monthly."),
        ("user", "q5"),
    ]


def test_estimate_tokens_counts_thai_cjk_digits_and_symbols_by_character_and_errs_high():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") == 4  # 11 characters: about 2-3 real tokens
    assert estimate_tokens("สวัสดี") == 6
    assert estimate_tokens("租期多久") == 4
    assert estimate_tokens("iPhone保修") == 4
    assert estimate_tokens("2026") == 4  # Qwen splits a number into digits
    assert estimate_tokens("a{}") == 3


def _history(n: int, answer: str = "a" * 300) -> list[tuple[str, str]]:
    return [(f"q{i}", answer) for i in range(n)]


def _history_tokens(messages) -> int:
    return sum(estimate_tokens(m["content"]) for m in messages[1:-1])


def test_answer_mode_history_takes_at_most_a_quarter_of_the_room_newest_first():
    messages = build_messages("q", _history(40), [_record(1)], "en", room=4000)
    asked = [m["content"] for m in messages[1:-1:2]]
    assert asked[-1] == "q39" and "q0" not in asked  # the oldest are dropped first
    assert asked == [f"q{i}" for i in range(40 - len(asked), 40)]  # contiguous
    assert _history_tokens(messages) <= 1000


def test_chat_mode_history_fills_the_room_the_prompt_leaves():
    answer_mode = build_messages("q", _history(40), [_record(1)], "en", room=4000)
    chat_mode = chat_messages("q", _history(40), "en", room=4000)
    assert len(chat_mode) > 2 * len(answer_mode)
    assert 2500 < _history_tokens(chat_mode) <= 4000


def test_a_larger_room_keeps_more_history():
    small = chat_messages("q", _history(40), "en", room=2000)
    large = chat_messages("q", _history(40), "en", room=30_000)
    assert len(small) < len(large) == 2 + 2 * 40


def test_the_newest_exchange_is_cut_to_fit_rather_than_dropped():
    history = [("old", "kept out"), ("what is the rent?", "x" * 30_000 + " [1]")]
    messages = chat_messages("and the deposit?", history, "en", room=2000)
    assert [m["content"] for m in messages[1::2]] == ["what is the rent?", "and the deposit?"]
    assert 0 < len(messages[2]["content"]) < 30_000
    assert set(messages[2]["content"]) == {"x"}


def test_a_long_earlier_answer_is_kept_whole_when_it_fits():
    answer = "The deposit is two months of rent. " * 40
    messages = chat_messages("and the rent?", [("deposit?", answer)], "en")
    assert messages[2]["content"] == answer.strip()


def test_the_routes_context_window_sets_the_room(env):
    store, make, chat = env
    long_answer = FakeLLM(("word " * 2000,))
    list(make(long_answer).ask(chat.id, "tell me everything", mode="chat"))
    small, large = FakeLLM(("ok",)), FakeLLM(("ok",))
    list(make(small, context_tokens=2048).ask(chat.id, "and then?", mode="chat"))
    list(make(large, context_tokens=32_000).ask(chat.id, "and then?", mode="chat"))
    kept_small = small.calls[0][2]["content"]
    kept_large = large.calls[0][2]["content"]
    assert len(kept_small) < len(kept_large) == len(("word " * 2000).strip())


def test_build_messages_skips_not_found_replies():
    history = [("q1", NOT_FOUND["en"]), ("q2", NOT_FOUND["th"]), ("q3", "Rent is due [1].")]
    messages = build_messages("q4", history, [_record(1)], "en")
    assert [m["content"] for m in messages[1:]] == ["q3", "Rent is due.", "q4"]


def test_the_system_prompt_lets_earlier_messages_say_what_a_question_means():
    system = build_messages("q", [], [_record(1)], "en")[0]["content"]
    assert "Earlier messages" in system


def test_cancel_stops_generation_and_keeps_the_partial_answer(env):
    store, make, chat = env
    service = make(FakeLLM(("one ", "two ", "three ")))
    stream = service.ask(chat.id, "How long is the lease term?")
    assert next(stream)["type"] == "sources"
    assert next(stream)["type"] == "context"
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
        label = "broken"

        def generate(self, messages, max_tokens=1024, *, think=False):
            yield Chunk("text", "partial ")
            raise LLMError("HTTP 500: boom")

    events = list(make(Broken()).ask(chat.id, "How long is the lease term?"))
    assert [e["type"] for e in events][-2:] == ["error", "done"]
    assert events[-2] == {"type": "error", "message": "HTTP 500: boom", "reason": "other"}
    assert store.list_messages(chat.id)[1].content == "partial "


def test_a_server_that_cannot_start_is_reported(env):
    store, make, chat = env

    def broken():
        raise LlamaServerError("Local model not found: m.gguf")

    events = list(make(broken).ask(chat.id, "How long is the lease term?"))
    assert events[-1] == {
        "type": "error",
        "message": "Local model not found: m.gguf",
        "reason": "other",
    }
    assert [m.role for m in store.list_messages(chat.id)] == ["user"]


def test_problems_before_answering_are_single_error_events(tmp_path):
    store = Store.open(tmp_path / "t.db")
    service = AnswerService(store, lambda t: None, lambda: Route("local", FakeLLM), model_id="m")
    chat = store.create_chat()
    assert list(service.ask(chat.id, "q")) == [
        {"type": "error", "message": "Choose a folder of documents first."}
    ]
    collection = store.create_collection("Docs", "C:/docs", "other-model")
    store.set_chat_scope(chat.id, [collection.id])
    assert list(service.ask(chat.id, "q"))[0]["message"].startswith("The index of ")
    store.reset_index(collection.id, "m")
    assert list(service.ask(999, "q")) == [{"type": "error", "message": "Chat 999 not found."}]
    store.close()


def test_asking_before_anything_is_indexed_saves_nothing(tmp_path):
    store = Store.open(tmp_path / "t.db")
    service = AnswerService(store, lambda t: None, lambda: Route("local", FakeLLM), model_id="m")
    collection = store.create_collection("Docs", "C:/docs", "m")
    store.add_file(collection.id, "a.md", 1, 1.0)  # pending, not indexed
    chat = store.create_chat(collection_id=collection.id)
    assert list(service.ask(chat.id, "q")) == [
        {
            "type": "error",
            "message": "No documents are indexed yet. Wait for indexing to finish, then ask again.",
        }
    ]
    assert store.list_messages(chat.id) == []
    store.close()


def test_detect_language():
    assert detect_language("ลาพักร้อนได้กี่วัน") == "th"
    assert detect_language("沙发保修几年？") == "zh"
    assert detect_language("How many days?") == "en"
    assert detect_language("1234 ?") == "en"
    assert detect_language("Tamra ตอบคำถามจากเอกสาร") == "th"


def test_detect_language_counts_latin_words_not_letters():
    assert detect_language("ChatGPT可以用吗？") == "zh"
    assert detect_language("iPhone保修多久") == "zh"
    assert detect_language("ลา sick leave ได้กี่วัน") == "th"
    assert detect_language("How many days of leave?") == "en"


def test_location_labels():
    pdf = {"kind": "pdf", "page_start": 3, "page_end": 3, "char_start": 0, "char_end": 5}
    assert location_label(pdf) == "p. 3"
    assert location_label({**pdf, "page_end": 4}) == "pp. 3-4"
    assert location_label({**pdf, "ocr": True}) == "p. 3 · OCR"
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


def src(n, text):
    location = {"kind": "text", "line_start": 1, "line_end": 1}
    return SourceRecord(n, None, None, f"f{n}.md", text, location, None)


def test_cited_numbers_keeps_only_valid_markers():
    assert cited_numbers("A [1] b [3] c [9] d [0]", 3) == [1, 3]
    assert cited_numbers("no markers", 3) == []


def test_cited_numbers_reads_marker_groups():
    assert cited_numbers("A [1, 2] b [3 ,9] c [2]", 3) == [1, 2, 3, 2]
    assert cited_numbers("A [1, 2, 3]", 2) == [1, 2]


def test_auto_citation_finds_the_source_an_answer_was_taken_from():
    sources = [
        src(1, "Parking costs fifty baht per day."),
        src(2, "The monthly rent is 18,500 baht, due on the 5th day of each month."),
    ]
    assert auto_citation("The monthly rent is 18,500 baht, due on the 5th.", sources) == 2


def test_auto_citation_works_for_thai_and_chinese():
    thai = [src(1, "พนักงานที่ผ่านการทดลองงานแล้วมีสิทธิลาพักร้อนปีละ 12 วันทำงาน")]
    assert auto_citation("พนักงานลาพักร้อนได้ปีละ 12 วัน", thai) == 1
    chinese = [src(1, "沙发框架保修五年，布料和海绵保修两年。")]
    assert auto_citation("沙发框架保修五年。", chinese) == 1


def test_auto_citation_leaves_cited_unrelated_and_empty_answers_alone():
    sources = [src(1, "The monthly rent is 18,500 baht.")]
    assert auto_citation("The monthly rent is 18,500 baht [1].", sources) is None
    assert auto_citation("The monthly rent is 18,500 baht [1, 2].", sources) is None
    assert auto_citation("Bananas are yellow and grow in bunches.", sources) is None
    assert auto_citation("ok", sources) is None
    assert auto_citation("The monthly rent is 18,500 baht.", []) is None


def test_an_uncited_answer_gets_a_citation_from_its_source(env):
    store, make, chat = env
    events = list(
        make(FakeLLM(("The lease term is three years.",))).ask(
            chat.id, "How long is the lease term?"
        )
    )
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert tokens == ["The lease term is three years.", " [1]"]
    assert store.list_messages(chat.id)[1].content == "The lease term is three years. [1]"


def test_a_cancelled_answer_gets_no_automatic_citation(env):
    store, make, chat = env
    service = make(FakeLLM(("The lease term ", "is three years.")))
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)  # sources
    next(stream)  # context
    next(stream)  # first token
    service.cancel()
    assert [e["type"] for e in stream] == ["done"]
    assert store.list_messages(chat.id)[1].content == "The lease term "


def test_thinking_chunks_are_not_part_of_the_answer(env):
    store, make, chat = env

    class Thinker:
        label = "thinker"

        def generate(self, messages, max_tokens=1024, *, think=False):
            yield Chunk("thinking", "Let me check the lease.")
            yield Chunk("text", "Three years [1].")

    events = list(make(Thinker()).ask(chat.id, "How long is the lease term?"))
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "Three years [1]."
    assert store.list_messages(chat.id)[1].content == "Three years [1]."


def test_thinking_chunks_become_events_and_think_is_passed_on(env):
    store, make, chat = env
    llm = FakeLLM((Chunk("thinking", "Let me check."), Chunk("text", "Three years [1].")))
    events = list(make(llm).ask(chat.id, "How long is the lease term?", think=True))
    assert [e["type"] for e in events] == ["sources", "context", "thinking", "token", "done"]
    assert events[2] == {"type": "thinking", "text": "Let me check."}
    assert llm.think_flags == [True]
    assert store.list_messages(chat.id)[1].content == "Three years [1]."
    list(make(llm).ask(chat.id, "How long is the lease term?"))
    assert llm.think_flags == [True, False]


def test_search_mode_returns_the_passages_without_calling_the_llm(env):
    store, make, chat = env
    llm = FakeLLM()
    events = list(make(llm).ask(chat.id, "How long is the lease term?", mode="search"))
    assert [e["type"] for e in events] == ["sources", "done"]
    assert events[0]["sources"][0]["file"] == "lease.md"
    assert llm.calls == []
    saved = store.list_messages(chat.id)[1]
    assert (saved.role, saved.content, saved.provider, saved.model) == ("assistant", "", None, None)
    assert [s.n for s in saved.sources] == [1, 2]
    assert events[-1]["message_id"] == saved.id


def test_search_mode_needs_no_model(env):
    store, make, chat = env

    def no_model():
        raise ProviderError("No local model is installed.", "model_missing")

    events = list(make(no_model).ask(chat.id, "How long is the lease term?", mode="search"))
    assert [e["type"] for e in events] == ["sources", "done"]


def test_an_unknown_mode_is_refused(env):
    store, make, chat = env
    with pytest.raises(ValueError, match="unknown mode"):
        list(make(FakeLLM()).ask(chat.id, "lease?", mode="poem"))


def test_chat_mode_asks_the_model_without_sources(env):
    store, make, chat = env
    llm = FakeLLM(("Paris is the capital of France.",))
    events = list(make(llm).ask(chat.id, "What is the capital of France?", mode="chat"))
    assert [e["type"] for e in events] == ["context", "token", "done"]
    system = llm.calls[0][0]["content"]
    assert "cannot see the user's" in system and "Sources:" not in system
    saved = store.list_messages(chat.id)[1]
    assert (saved.content, saved.provider, saved.model) == (
        "Paris is the capital of France.",
        "local",
        "fake-llm",
    )
    assert not saved.sources


def test_chat_mode_needs_no_indexed_project(env):
    store, make, _ = env
    empty = store.create_collection("Empty", "C:/empty", "m")
    chat = store.create_chat(collection_id=empty.id)
    refused = list(make(FakeLLM()).ask(chat.id, "hello"))
    assert refused[-1]["type"] == "error"
    events = list(make(FakeLLM(("Hi!",))).ask(chat.id, "hello", mode="chat"))
    assert [e["type"] for e in events] == ["context", "token", "done"]


def test_chat_mode_adds_no_citation_even_when_the_reply_repeats_a_document(env):
    store, make, chat = env
    llm = FakeLLM(("The lease term is three years",))
    events = list(make(llm).ask(chat.id, "lease?", mode="chat"))
    assert "".join(e["text"] for e in events if e["type"] == "token") == (
        "The lease term is three years"
    )


def test_chat_messages_keep_the_earlier_exchanges_and_the_language():
    history = [("q1", "The lease is three years [1]."), ("q2", NOT_FOUND["th"])]
    messages = chat_messages("q3", history, "th")
    assert "Thai" in messages[0]["content"]
    assert [m["content"] for m in messages[1:]] == ["q1", "The lease is three years.", "q3"]


def test_the_context_event_tells_the_prompt_size_window_and_history_kept(env):
    store, make, chat = env
    llm = FakeLLM(("Three years.",))
    service = make(llm)
    list(service.ask(chat.id, "How long is the lease term?"))
    events = list(service.ask(chat.id, "and parking?", mode="chat"))
    context = events[0]
    assert context["type"] == "context"
    assert context["context_tokens"] == 8192
    assert (context["history_kept"], context["history_total"]) == (1, 1)
    sent = llm.calls[-1]
    assert context["prompt_tokens"] == sum(estimate_tokens(m["content"]) + 8 for m in sent)


def test_usage_chunks_become_throttled_usage_events_ending_with_the_final_counts(env):
    store, make, chat = env
    llm = FakeLLM(
        (
            Chunk("usage", usage=Usage(100, 1, 0.0)),
            "Three ",
            Chunk("usage", usage=Usage(100, 2, 30.0)),
            "years.",
            Chunk("usage", usage=Usage(100, 3, 31.25)),
        )
    )
    events = list(make(llm).ask(chat.id, "lease?", mode="chat"))
    usage = [e for e in events if e["type"] == "usage"]
    assert usage[0] == {
        "type": "usage",
        "prompt_tokens": 100,
        "generated_tokens": 1,
        "tokens_per_second": 0.0,
    }
    assert usage[-1]["generated_tokens"] == 3 and usage[-1]["tokens_per_second"] == 31.2
    assert len(usage) == 2  # the middle one came too soon after the first
    assert store.list_messages(chat.id)[1].content == "Three years."


def test_thinking_reserves_the_providers_extra_reply_room(env):
    store, make, chat = env
    long_answer = FakeLLM(("word " * 3000,))
    list(make(long_answer).ask(chat.id, "tell me everything", mode="chat"))
    plain, thinking = FakeLLM(("ok",)), FakeLLM(("ok",))
    list(make(plain).ask(chat.id, "and then?", mode="chat"))
    list(make(thinking).ask(chat.id, "and then?", mode="chat", think=True))
    kept_plain = estimate_tokens(plain.calls[0][2]["content"])
    kept_thinking = estimate_tokens(thinking.calls[0][2]["content"])
    assert kept_plain - kept_thinking >= THINKING_HEADROOM - 16


def test_a_missing_local_model_is_reported_with_its_reason(env):
    store, make, chat = env

    def no_model():
        raise ProviderError("No local model is installed.", "model_missing")

    events = list(make(no_model).ask(chat.id, "How long is the lease term?"))
    assert [e["type"] for e in events] == ["sources", "context", "error"]
    assert events[-1] == {
        "type": "error",
        "message": "No local model is installed.",
        "reason": "model_missing",
    }
    assert [m.role for m in store.list_messages(chat.id)] == ["user"]


def test_provider_errors_keep_their_reason_and_the_partial_answer(env):
    store, make, chat = env

    class OutOfCredit:
        label = "claude-test"

        def generate(self, messages, max_tokens=1024, *, think=False):
            yield Chunk("text", "partial ")
            raise ProviderError("HTTP 429: slow down", "quota")

    events = list(make(OutOfCredit(), name="anthropic").ask(chat.id, "lease term?"))
    assert events[-2] == {"type": "error", "message": "HTTP 429: slow down", "reason": "quota"}
    saved = store.list_messages(chat.id)[1]
    assert (saved.content, saved.provider, saved.model) == ("partial ", "anthropic", "claude-test")


def test_an_api_answer_is_saved_with_its_provider_and_model(env):
    store, make, chat = env
    llm = FakeLLM(kind="api", label="claude-sonnet-5-5")
    list(make(llm, name="anthropic").ask(chat.id, "How long is the lease term?"))
    saved = store.list_messages(chat.id)[1]
    assert (saved.provider, saved.model) == ("anthropic", "claude-sonnet-5-5")


@pytest.fixture
def many(tmp_path):
    """Ten passages that all match "lease term", to show how many the answer takes."""
    store = Store.open(tmp_path / "t.db")
    collection = store.create_collection("Docs", "C:/docs", "m")
    embedder = FakeEmbedder()
    texts = [f"lease term clause {word}" for word in "abcdefghij"]
    file_id = store.add_file(collection.id, "lease.md", 1, 1.0)
    chunks = [
        ChunkInput(t, {"kind": "text", "line_start": i + 1, "line_end": i + 1})
        for i, t in enumerate(texts)
    ]
    store.replace_file_chunks(file_id, chunks, embedder.embed(texts), content_hash="h", note=None)

    def make(name):
        return AnswerService(
            store,
            lambda text: embedder.embed([text])[0],
            lambda: Route(name, lambda: FakeLLM(("Clause [1].",))),
            model_id="m",
            settings=AnswerSettings(min_similarity=0.3),
        )

    yield store, make, store.create_chat(collection_id=collection.id)
    store.close()


@pytest.mark.parametrize(("name", "expected"), [("local", 4), ("anthropic", 8), ("openai", 8)])
def test_the_context_budget_depends_on_the_provider(many, name, expected):
    store, make, chat = many
    events = list(make(name).ask(chat.id, "lease term?"))
    assert len(events[0]["sources"]) == expected
    assert len(store.list_messages(chat.id)[1].sources) == expected


def test_search_mode_uses_the_same_budget(many):
    store, make, chat = many
    events = list(make("anthropic").ask(chat.id, "lease term?", mode="search"))
    assert len(events[0]["sources"]) == 8


def test_when_idle_runs_at_once_when_no_answer_is_running(env):
    store, make, chat = env
    ran = []
    make(FakeLLM()).when_idle(lambda: ran.append("now"))
    assert ran == ["now"]


def test_when_idle_waits_for_the_running_answer_to_end(env):
    store, make, chat = env
    service = make(FakeLLM())
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)
    ran = []
    service.when_idle(lambda: ran.append("later"))
    assert ran == []
    list(stream)
    assert ran == ["later"]


def test_run_if_idle_runs_only_when_no_answer_is_being_written(env):
    store, make, chat = env
    service = make(FakeLLM())
    ran = []
    assert service.run_if_idle(lambda: ran.append("idle")) is True
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)
    assert service.run_if_idle(lambda: ran.append("busy")) is False
    list(stream)
    assert service.run_if_idle(lambda: ran.append("again")) is True
    assert ran == ["idle", "again"]


def test_a_failing_deferred_action_does_not_stop_the_others(env):
    store, make, chat = env
    service = make(FakeLLM())
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)
    ran = []

    def boom():
        raise RuntimeError("close failed")

    service.when_idle(boom)
    service.when_idle(lambda: ran.append("second"))
    assert [e["type"] for e in stream][-1] == "done"
    assert ran == ["second"]


def test_a_new_answer_cannot_start_while_deferred_actions_are_running(env):
    store, make, chat = env
    service = make(FakeLLM())
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)
    seen = []
    service.when_idle(lambda: seen.append(list(service.ask(chat.id, "lease?"))))
    list(stream)
    assert seen == [[{"type": "error", "message": "Another answer is still being written."}]]
    assert [e["type"] for e in service.ask(chat.id, "lease term?")][-1] == "done"  # then free


def test_an_action_deferred_while_draining_still_runs(env):
    store, make, chat = env
    service = make(FakeLLM())
    stream = service.ask(chat.id, "How long is the lease term?")
    next(stream)
    ran = []
    service.when_idle(lambda: service.when_idle(lambda: ran.append("nested")))
    list(stream)
    assert ran == ["nested"]


def test_an_answer_with_only_thinking_is_reported_not_silent(env):
    store, make, chat = env
    llm = FakeLLM((Chunk("thinking", "Hmm."),))
    events = list(make(llm).ask(chat.id, "How long is the lease term?", think=True))
    assert [e["type"] for e in events] == ["sources", "context", "thinking", "error"]
    assert events[-1] == {
        "type": "error",
        "message": "The model returned no answer.",
        "reason": "other",
    }
    assert [m.role for m in store.list_messages(chat.id)] == ["user"]


# Answers over a chat's scope (spec section 6, M4)


@pytest.fixture
def two(tmp_path):
    """Two projects, A about the lease and B about parking, and a service over them."""
    store = Store.open(tmp_path / "t.db")
    embedder = FakeEmbedder()
    projects = {}
    for name, text in (("A", "The lease term is three years"), ("B", "Parking costs fifty baht")):
        collection = store.create_collection(name, f"C:/{name}", "m")
        file_id = store.add_file(collection.id, f"{name.lower()}.md", 1, 1.0)
        chunk = ChunkInput(text, {"kind": "text", "line_start": 1, "line_end": 1})
        store.replace_file_chunks(
            file_id, [chunk], embedder.embed([text]), content_hash="h", note=None
        )
        projects[name] = collection

    def make(llm):
        return AnswerService(
            store,
            lambda text: embedder.embed([text])[0],
            lambda: Route("local", lambda: llm),
            model_id="m",
            settings=AnswerSettings(min_similarity=0.3),
        )

    yield store, make, projects
    store.close()


def _sources(events):
    return next(e["sources"] for e in events if e["type"] == "sources")


def test_a_chat_scoped_to_one_project_finds_only_its_passages(two):
    store, make, projects = two
    chat = store.create_chat(collection_id=projects["A"].id)
    events = list(make(FakeLLM()).ask(chat.id, "lease term parking baht"))
    assert [(s["file"], s["project"]) for s in _sources(events)] == [("a.md", "A")]
    assert not [e for e in events if e["type"] == "notice"]


def test_a_chat_scoped_to_two_projects_cites_both(two):
    store, make, projects = two
    chat = store.create_chat(collection_id=projects["A"].id)
    store.set_chat_scope(chat.id, [projects["A"].id, projects["B"].id])
    llm = FakeLLM(("The lease is three years [1]. Parking is fifty baht [2].",))
    events = list(make(llm).ask(chat.id, "lease term and parking baht"))
    assert {(s["file"], s["project"]) for s in _sources(events)} == {("a.md", "A"), ("b.md", "B")}
    saved = store.list_messages(chat.id)[1]
    assert cited_numbers(saved.content, len(saved.sources)) == [1, 2]
    assert {s.rel_path for s in saved.sources} == {"a.md", "b.md"}


def test_the_not_found_gate_uses_the_best_similarity_across_the_scope(two):
    store, make, projects = two
    chat = store.create_chat(collection_id=projects["A"].id)
    store.set_chat_scope(chat.id, [projects["A"].id, projects["B"].id])
    llm = FakeLLM()
    events = list(make(llm).ask(chat.id, "parking baht"))  # only B matches
    assert _sources(events)[0]["file"] == "b.md"
    assert len(llm.calls) == 1
    other = store.create_chat(collection_id=projects["A"].id)
    store.set_chat_scope(other.id, [projects["A"].id, projects["B"].id])
    events = list(make(llm).ask(other.id, "ใครเป็นผู้จัดการฝ่ายขาย"))
    assert _sources(events) == []
    assert len(llm.calls) == 1


def test_a_stale_project_is_skipped_with_a_notice_before_the_sources(two):
    store, make, projects = two
    store.reset_index(projects["B"].id, "other-model")
    chat = store.create_chat(collection_id=projects["A"].id)
    store.set_chat_scope(chat.id, [projects["A"].id, projects["B"].id])
    events = list(make(FakeLLM()).ask(chat.id, "lease term parking baht"))
    assert events[0] == {"type": "notice", "code": "stale_projects", "names": ["B"]}
    assert events[1]["type"] == "sources"
    assert [s["project"] for s in events[1]["sources"]] == ["A"]
    assert events[-1]["type"] == "done"


def test_a_scope_of_only_stale_projects_asks_for_a_rebuild(two):
    store, make, projects = two
    store.reset_index(projects["A"].id, "other-model")
    chat = store.create_chat(collection_id=projects["A"].id)
    events = list(make(FakeLLM()).ask(chat.id, "lease term"))
    assert [e["type"] for e in events] == ["error"]
    assert events[0]["message"].startswith("The index of A was built with")
    assert store.list_messages(chat.id) == []


def test_a_stale_project_is_named_even_when_the_rest_is_not_indexed_yet(two):
    store, make, projects = two
    store.reset_index(projects["A"].id, "other-model")
    store.reset_index(projects["B"].id, projects["B"].embedding_model_id)  # pending again
    chat = store.create_chat(collection_id=projects["A"].id)
    store.set_chat_scope(chat.id, [projects["A"].id, projects["B"].id])
    events = list(make(FakeLLM()).ask(chat.id, "lease term"))
    assert events[0] == {"type": "notice", "code": "stale_projects", "names": ["A"]}
    assert events[1]["type"] == "error" and "No documents are indexed yet" in events[1]["message"]


def test_an_empty_scope_falls_back_to_the_home_project(two):
    store, make, projects = two
    chat = store.create_chat(collection_id=projects["B"].id)
    with store._lock:
        store._conn.execute("DELETE FROM chat_collections WHERE chat_id = ?", (chat.id,))
    assert store.get_chat(chat.id).scope == []
    events = list(make(FakeLLM()).ask(chat.id, "lease term parking baht"))
    assert [(s["file"], s["project"]) for s in _sources(events)] == [("b.md", "B")]


def test_a_homeless_chat_with_an_empty_scope_searches_nothing(two):
    store, make, projects = two
    chat = store.create_chat()
    events = list(make(FakeLLM()).ask(chat.id, "lease term"))
    assert [e["type"] for e in events] == ["error"]
    assert events[0]["message"].startswith("No documents are indexed yet")
    assert store.list_messages(chat.id) == []


def test_a_scope_with_nothing_indexed_gives_the_not_indexed_error(two):
    store, make, projects = two
    empty = store.create_collection("C", "C:/C", "m")
    store.add_file(empty.id, "c.md", 1, 1.0)  # pending
    chat = store.create_chat(collection_id=empty.id)
    events = list(make(FakeLLM()).ask(chat.id, "lease term"))
    assert events[0]["message"].startswith("No documents are indexed yet")


def test_saved_sources_carry_the_project_name_or_none_once_the_file_is_gone(two):
    store, make, projects = two
    chat = store.create_chat(collection_id=projects["A"].id)
    list(make(FakeLLM()).ask(chat.id, "lease term"))
    saved = store.list_messages(chat.id)[1]
    assert [p["project"] for p in source_payloads(store, saved.sources)] == ["A"]
    store.delete_file(saved.sources[0].file_id)
    saved = store.list_messages(chat.id)[1]
    assert [p["project"] for p in source_payloads(store, saved.sources)] == [None]


@pytest.mark.parametrize(
    ("error", "reason"),
    [(EmbedderUnavailable("model.onnx not found"), "embedding_missing"), (MemoryError(), None)],
)
def test_a_question_the_search_model_cannot_embed_is_not_saved(env, error, reason):
    store, make, chat = env

    def failing(text):
        raise error

    service = AnswerService(
        store, failing, lambda: Route("local", FakeLLM), model_id="m", settings=AnswerSettings()
    )
    events = list(service.ask(chat.id, "What is the lease term?"))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0].get("reason") == reason  # only missing files send the user to Settings
    assert events[0]["message"].startswith("Embedding model unavailable")
    assert store.list_messages(chat.id) == []


# Retrieval without a chat (shared with the MCP search)


def test_searchable_leaves_out_stale_projects_and_names_them(two):
    store, make, projects = two
    store.reset_index(projects["B"].id, "other-model")
    searched, skipped = make(FakeLLM()).searchable(store.list_collections())
    assert [c.name for c in searched] == ["A"]
    assert skipped == ["B"]


def test_searchable_raises_stale_index_when_every_project_is_stale(two):
    store, make, projects = two
    store.reset_index(projects["A"].id, "other-model")
    with pytest.raises(StaleIndex) as raised:
        make(FakeLLM()).searchable([store.get_collection(projects["A"].id)])
    assert raised.value.names == ["A"]


def test_searchable_raises_nothing_indexed_with_the_stale_names(two):
    store, make, projects = two
    store.reset_index(projects["A"].id, "other-model")
    store.reset_index(projects["B"].id, "m")  # pending again
    with pytest.raises(NothingIndexed) as raised:
        make(FakeLLM()).searchable(store.list_collections())
    assert raised.value.skipped == ["A"]
    with pytest.raises(NothingIndexed):
        make(FakeLLM()).searchable([])


def test_passages_are_numbered_best_first_and_cut_at_k(two):
    store, make, projects = two
    vector = FakeEmbedder().embed(["parking baht"])[0]
    found = make(FakeLLM()).passages(
        [projects["A"].id, projects["B"].id], vector, "parking baht", 1
    )
    assert [(s.n, s.rel_path, s.text) for s in found] == [(1, "b.md", "Parking costs fifty baht")]


def test_passages_are_empty_below_the_not_found_gate(two):
    store, make, projects = two
    query = "ใครเป็นผู้จัดการฝ่ายขาย"
    vector = FakeEmbedder().embed([query])[0]
    ids = [projects["A"].id, projects["B"].id]
    assert make(FakeLLM()).passages(ids, vector, query, 4) == []
