"""Answering (spec §6): retrieve, prompt with numbered sources, stream, and save the answer."""

import logging
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

import numpy as np

from tamra.llm.base import LLMError, Message, Provider
from tamra.llm.llama_server import LlamaServerError
from tamra.retriever import best_similarity, fts_query, hybrid_search, query_text, trigrams
from tamra.store import SourceRecord, Store

log = logging.getLogger(__name__)

_LATIN_WORD = re.compile(r"[A-Za-z]+")
LANGUAGE_NAMES = {"th": "Thai", "en": "English", "zh": "Chinese"}
NOT_FOUND = {
    "th": "ไม่พบข้อมูลนี้ในเอกสาร",
    "en": "Not found in the documents.",
    "zh": "文档中未找到相关信息。",
}
SYSTEM_PROMPT = """You answer questions using only the numbered sources below.
Rules:
- Use only facts stated in the sources. If they do not answer the question, say so plainly.
- Cite each sentence that uses a source with its number in brackets, like [1] or [2][3].
- Write the whole answer in {language}, the language of the question.
- Be concise.

Sources:

{sources}

Example answer format: The rent is 10,000 baht [1]."""

AUTO_CITE_MIN_OVERLAP = 0.5  # share of the answer's trigrams found in one source
_MARKER = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class Route:
    """Where one question goes: the provider's name, and a way to get it ready.

    `name` is cheap to know and decides the context budget; it is saved with the answer
    ("local", "anthropic" or "openai"). `open` may be slow (it can start llama-server) or fail
    with a ProviderError (no model installed, no API key), so it is only called when the answer
    needs the model.
    """

    name: str
    open: Callable[[], Provider]


@dataclass(frozen=True)
class AnswerSettings:
    top_k: int = 4  # spec §6: about 4 sources for small local models
    api_top_k: int = 8  # spec §6: 8-10 for API models, which have a large context
    # below this best dense similarity: "not found" (M1 eval; docs/spikes/2026-10-m1-results.md)
    min_similarity: float = 0.43
    max_tokens: int = 1024


def detect_language(text: str) -> str:
    """'th', 'zh', or 'en': whichever script dominates (English when there is no script).

    Thai and CJK are counted by character, Latin by word: a Latin brand name inside a Chinese
    or Thai question ("iPhone保修多久") must not outweigh the characters around it.
    """
    thai = sum(1 for c in text if 0x0E00 <= ord(c) <= 0x0E7F)
    cjk = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF or 0x3400 <= ord(c) <= 0x4DBF)
    latin = len(_LATIN_WORD.findall(text))
    count, language = max((thai, "th"), (cjk, "zh"), (latin, "en"))
    return language if count else "en"


def cited_numbers(text: str, source_count: int) -> list[int]:
    """The [n] markers in text that name one of the sources."""
    return [n for n in map(int, _MARKER.findall(text)) if 1 <= n <= source_count]


def auto_citation(answer: str, sources: list[SourceRecord]) -> int | None:
    """The source an uncited answer was taken from, by character-trigram overlap.

    Small local models often answer from a source without writing its [n]. Returns None
    when the answer already cites a source or no single source clearly contains it.
    """
    if not sources or cited_numbers(answer, len(sources)):
        return None
    grams = set(trigrams(answer))
    if not grams:
        return None
    best_n, best_score = None, 0.0
    for source in sources:
        score = len(grams & set(trigrams(source.text))) / len(grams)
        if score > best_score:
            best_n, best_score = source.n, score
    return best_n if best_score >= AUTO_CITE_MIN_OVERLAP else None


def location_label(location: dict) -> str:
    """A short human-readable location: p. 3, Heading > Sub, para. 5, lines 7-9."""
    kind = location.get("kind")
    if kind == "pdf":
        start, end = location["page_start"], location["page_end"]
        return f"p. {start}" if start == end else f"pp. {start}-{end}"
    if kind == "docx":
        start, end = location["paragraph_start"] + 1, location["paragraph_end"] + 1
        paragraphs = f"para. {start}" if start == end else f"paras. {start}-{end}"
        path = " > ".join(location.get("heading_path") or [])
        return f"{path}, {paragraphs}" if path else paragraphs
    if kind == "text":
        start, end = location["line_start"], location["line_end"]
        return f"line {start}" if start == end else f"lines {start}-{end}"
    return ""


def source_payload(source: SourceRecord) -> dict:
    """What the UI shows for a source."""
    return {
        "n": source.n,
        "file": source.rel_path,
        "label": location_label(source.location),
        "text": source.text,
        "location": source.location,
    }


def build_messages(
    question: str,
    previous: tuple[str | None, str | None],
    sources: list[SourceRecord],
    language: str,
) -> list[Message]:
    blocks = "\n\n".join(
        f"[{s.n}] {s.rel_path} ({location_label(s.location)})\n{s.text}" for s in sources
    )
    messages: list[Message] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(language=LANGUAGE_NAMES[language], sources=blocks),
        }
    ]
    previous_question, previous_answer = previous
    if previous_question and previous_answer:
        messages.append({"role": "user", "content": previous_question})
        messages.append({"role": "assistant", "content": previous_answer[:600]})
    messages.append({"role": "user", "content": question})
    return messages


class AnswerService:
    def __init__(
        self,
        store: Store,
        embed_query: Callable[[str], np.ndarray],
        route: Callable[[], Route],
        *,
        model_id: str,
        settings: AnswerSettings | None = None,
    ):
        self._store = store
        self._embed_query = embed_query
        self._route = route
        self._model_id = model_id
        self._settings = settings or AnswerSettings()
        self._state = threading.Lock()  # guards _answering and _after
        self._answering = False
        self._after: list[Callable[[], None]] = []
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Stop the answer being written; what was written so far is kept."""
        self._cancel.set()

    def when_idle(self, action: Callable[[], None]) -> None:
        """Run action now, or, if an answer is being written, right after it ends.

        Used to close a provider that is being replaced without cutting off the stream that
        is still reading from it.
        """
        with self._state:
            if self._answering:
                self._after.append(action)
                return
        _run(action)

    def ask(
        self,
        chat_id: int,
        question: str,
        *,
        mode: Literal["answer", "search"] = "answer",
        think: bool = False,
    ) -> Iterator[dict]:
        if mode not in ("answer", "search"):
            raise ValueError(f"unknown mode: {mode!r}")
        with self._state:
            busy = self._answering
            self._answering = True
        if busy:  # _answering stays True: the answer that set it clears it
            yield {"type": "error", "message": "Another answer is still being written."}
            return
        self._cancel.clear()
        try:
            yield from self._ask(chat_id, question, mode, think)
        finally:
            with self._state:
                self._answering = False
                after, self._after = self._after, []
            for action in after:
                _run(action)

    def _ask(self, chat_id: int, question: str, mode: str, think: bool) -> Iterator[dict]:
        collection = self._store.get_collection()
        if collection is None:
            yield {"type": "error", "message": "Choose a folder of documents first."}
            return
        if collection.embedding_model_id != self._model_id:
            yield {
                "type": "error",
                "message": "The index was built with a different embedding model. "
                "Rebuild the index.",
            }
            return
        if self._store.get_chat(chat_id) is None:
            yield {"type": "error", "message": f"Chat {chat_id} not found."}
            return
        if self._store.status_counts(collection.id)["indexed"] == 0:
            yield {
                "type": "error",
                "message": "No documents are indexed yet. Wait for indexing to finish, "
                "then ask again.",
            }
            return
        route = self._route()
        previous = self._store.last_exchange(chat_id)
        self._store.add_user_message(chat_id, question)
        language = detect_language(question)
        query = query_text(question, previous[0])
        try:
            vector = self._embed_query(query)
        except Exception as e:  # the model files are missing or cannot be loaded
            yield {"type": "error", "message": f"Embedding model unavailable: {e}"}
            return
        hits = hybrid_search(self._store, collection.id, vector, fts_query(query))
        if best_similarity(hits) < self._settings.min_similarity:
            text = NOT_FOUND[language]
            message_id = self._store.add_assistant_message(
                chat_id, text, provider=None, model=None, sources=[]
            )
            yield {"type": "sources", "sources": []}
            yield {"type": "token", "text": text}
            yield {"type": "done", "message_id": message_id}
            return
        budget = self._settings.top_k if route.name == "local" else self._settings.api_top_k
        chunks = self._store.get_chunks([hit.chunk_id for hit in hits[:budget]])
        sources = [
            SourceRecord(n, c.id, c.file_id, c.rel_path, c.text, c.location, c.file_hash)
            for n, c in enumerate(chunks, start=1)
        ]
        yield {"type": "sources", "sources": [source_payload(s) for s in sources]}
        if mode == "search":  # passages only: no model is involved, so none is recorded
            message_id = self._store.add_assistant_message(
                chat_id, "", provider=None, model=None, sources=sources
            )
            yield {"type": "done", "message_id": message_id}
            return
        messages = build_messages(question, previous, sources, language)
        parts: list[str] = []
        error: str | None = None
        reason = "other"
        label = ""
        message_id: int | None = None
        try:
            try:
                llm = route.open()
                label = llm.label
                cancelled = False
                for chunk in llm.generate(messages, self._settings.max_tokens, think=think):
                    if self._cancel.is_set():
                        cancelled = True
                        break
                    if chunk.kind == "thinking":  # shown live, never saved with the answer
                        yield {"type": "thinking", "text": chunk.text}
                        continue
                    parts.append(chunk.text)
                    yield {"type": "token", "text": chunk.text}
                if not cancelled:
                    n = auto_citation("".join(parts), sources)
                    if n is not None:
                        marker = f" [{n}]"
                        parts.append(marker)
                        yield {"type": "token", "text": marker}
            except (LLMError, LlamaServerError) as e:
                error = str(e)
                reason = getattr(e, "reason", "other")  # ProviderError says why; others do not
        finally:  # also runs when the consumer closes the stream: keep what the user saw
            content = "".join(parts)
            if content:
                message_id = self._store.add_assistant_message(
                    chat_id, content, provider=route.name, model=label, sources=sources
                )
        if error:
            yield {"type": "error", "message": error, "reason": reason}
        if message_id is not None:
            yield {"type": "done", "message_id": message_id}


def _run(action: Callable[[], None]) -> None:
    try:
        action()
    except Exception:  # a failed close must not break the answer or skip the other actions
        log.exception("deferred action failed")
