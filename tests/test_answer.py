import pytest
from fakes import FakeEmbedder, FakeLLM

from tamra.answer import (
    AnswerService,
    AnswerSettings,
    Route,
    auto_citation,
    cited_numbers,
    detect_language,
    location_label,
    source_payloads,
)
from tamra.llm.base import Chunk, LLMError, ProviderError
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

    def make(llm, name="local", settings=None):
        def opened():
            return llm() if callable(llm) and not hasattr(llm, "generate") else llm

        return AnswerService(
            store,
            lambda text: embedder.embed([text])[0],
            lambda: Route(name, opened),
            model_id="m",
            settings=settings or AnswerSettings(min_similarity=0.3),
        )

    yield store, make, store.create_chat(collection_id=collection.id)
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
    assert list(service.ask(chat.id, "q"))[0]["message"].startswith("The index was built with")
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
    assert [e["type"] for e in events] == ["sources", "thinking", "token", "done"]
    assert events[1] == {"type": "thinking", "text": "Let me check."}
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
        list(make(FakeLLM()).ask(chat.id, "lease?", mode="chat"))


def test_a_missing_local_model_is_reported_with_its_reason(env):
    store, make, chat = env

    def no_model():
        raise ProviderError("No local model is installed.", "model_missing")

    events = list(make(no_model).ask(chat.id, "How long is the lease term?"))
    assert [e["type"] for e in events] == ["sources", "error"]
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
    assert [e["type"] for e in events] == ["sources", "thinking", "error"]
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
    assert events[0]["message"].startswith("The index was built with")
    assert store.list_messages(chat.id) == []


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
