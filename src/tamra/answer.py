"""Answering (spec §6): retrieve, prompt with numbered sources, stream, and save the answer."""

import logging
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from tamra.embedder import EmbedderUnavailable
from tamra.llm.base import THINKING_HEADROOM, LLMError, Message, Provider, Usage
from tamra.llm.llama_server import LlamaServerError
from tamra.retriever import best_similarity, fts_query, hybrid_search, query_text, trigrams
from tamra.store import ChunkRecord, Collection, SourceRecord, Store

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
- Earlier messages in the conversation tell what the question refers to; facts still come only
  from the sources.
- Cite each sentence that uses a source with its number in brackets, like [1] or [2][3].
- Write the whole answer in {language}, the language of the question.
- Be concise.

Sources:

{sources}

Example answer format: The rent is 10,000 baht [1]."""
# Chat mode (spec §6): no retrieval, so the reply must not pass for a cited answer.
CHAT_PROMPT = """You are a helpful assistant. In this conversation you cannot see the user's
documents.
Rules:
- Answer from general knowledge. Never say a fact comes from the user's documents, and do not
  write citation markers like [1].
- If you are not sure, say so plainly.
- Write the whole answer in {language}, the language of the question.
- Be concise."""

AUTO_CITE_MIN_OVERLAP = 0.5  # share of the answer's trigrams found in one source
# One citation marker: [1], [12] or a group such as [1, 2]. tamra.attribution and the UI
# (ui/src/citations.ts) read the same form.
MARKER_GROUP = r"\[\d{1,3}(?:\s*,\s*\d{1,3})*\]"
_MARKER = re.compile(MARKER_GROUP)
# A marker with the spaces before it, so "years [1]." becomes "years." without a double space.
_SPACED_MARKER = re.compile(r"[ \t]*" + MARKER_GROUP)
_NOT_FOUND_REPLIES = frozenset(NOT_FOUND.values())
MAX_HISTORY_TURNS = 50  # earlier exchanges read for the prompt; the token budget picks from them
LOCAL_CONTEXT_TOKENS = 8192  # llama-server's -c (tamra.llm.runtime.LocalLLM)
# API endpoints: a window every supported model has, including small OpenAI-compatible servers
API_CONTEXT_TOKENS = 32_000
ANSWER_HISTORY_SHARE = 0.25  # Answer mode: history gets at most this share of the prompt room
PROMPT_MARGIN = 256  # chat-template tokens and estimate error
MESSAGE_TOKENS = 8  # chat-template tokens around one message
USAGE_INTERVAL = 0.25  # seconds between usage events: the UI's counter, not every token
_DIGITS = re.compile(r"\d+")


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
    context_tokens: int = LOCAL_CONTEXT_TOKENS  # the model's context window: prompt and reply


@dataclass(frozen=True)
class AnswerSettings:
    top_k: int = 4  # spec §6: about 4 sources for small local models
    api_top_k: int = 8  # spec §6: 8-10 for API models, which have a large context
    # below this best dense similarity: "not found" (M1 eval; docs/spikes/2026-10-m1-results.md)
    min_similarity: float = 0.43
    max_tokens: int = 1024
    # fused candidates the reranker reorders before the top k are taken: each full chunk costs
    # ~0.45 s on a CPU, and every eval hit was in the fused top 10
    # (docs/spikes/2026-10-reranker-results.md)
    rerank_candidates: int = 10


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
    numbers = (int(n) for group in _MARKER.findall(text) for n in _DIGITS.findall(group))
    return [n for n in numbers if 1 <= n <= source_count]


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
        pages = f"p. {start}" if start == end else f"pp. {start}-{end}"
        return f"{pages} · OCR" if location.get("ocr") else pages
    if kind == "docx":
        start, end = location["paragraph_start"] + 1, location["paragraph_end"] + 1
        paragraphs = f"para. {start}" if start == end else f"paras. {start}-{end}"
        path = " > ".join(location.get("heading_path") or [])
        return f"{path}, {paragraphs}" if path else paragraphs
    if kind == "text":
        start, end = location["line_start"], location["line_end"]
        return f"line {start}" if start == end else f"lines {start}-{end}"
    return ""


def source_payload(source: SourceRecord, project: str | None = None) -> dict:
    """What the UI shows for a source. `project` is the name of the project its file belongs to
    (None once the file left the index)."""
    return {
        "n": source.n,
        "project": project,
        "file_id": source.file_id,  # None once the file left the index
        "file": source.rel_path,
        "label": location_label(source.location),
        "text": source.text,
        "location": source.location,
    }


def source_payloads(store: Store, sources: Sequence[SourceRecord]) -> list[dict]:
    """Payloads for sources, each labelled with its file's current project name."""
    names: dict[int, str | None] = {}
    payloads = []
    for source in sources:
        project = None
        if source.file_id is not None:
            if source.file_id not in names:
                file = store.get_file(source.file_id)
                collection = store.get_collection(file.collection_id) if file else None
                names[source.file_id] = collection.name if collection else None
            project = names[source.file_id]
        payloads.append(source_payload(source, project))
    return payloads


def build_messages(
    question: str,
    history: Sequence[tuple[str, str | None]],
    sources: list[SourceRecord],
    language: str,
    room: int = LOCAL_CONTEXT_TOKENS - AnswerSettings.max_tokens,
) -> list[Message]:
    """The prompt: system with the sources, the answered earlier exchanges (oldest first), then
    the question. Earlier answers lose their markers: their sources are no longer in the prompt.

    room is the prompt's share of the context window (the window less the reply's max tokens).
    History gets what the sources and question leave, at most ANSWER_HISTORY_SHARE of room: the
    sources come first, and a short prompt keeps a small model on them (spec §6)."""
    system = SYSTEM_PROMPT.format(language=LANGUAGE_NAMES[language], sources=_blocks(sources))
    budget = min(_left(room, system, question), int(room * ANSWER_HISTORY_SHARE))
    return _with_history(system, history, question, budget)


def _blocks(sources: list[SourceRecord]) -> str:
    """The numbered sources as the system prompt lists them."""
    return "\n\n".join(
        f"[{s.n}] {s.rel_path} ({location_label(s.location)})\n{s.text}" for s in sources
    )


def chat_messages(
    question: str,
    history: Sequence[tuple[str, str | None]],
    language: str,
    room: int = LOCAL_CONTEXT_TOKENS - AnswerSettings.max_tokens,
) -> list[Message]:
    """The chat-mode prompt: no sources, the earlier exchanges in all the room the system prompt
    and question leave, then the question."""
    system = CHAT_PROMPT.format(language=LANGUAGE_NAMES[language])
    return _with_history(system, history, question, _left(room, system, question))


def _is_light(c: str) -> bool:
    """A Latin-script letter or a space: tokenizers merge about three or four into one token.
    Thai and CJK characters (and CJK punctuation), digits (Qwen splits numbers into single
    digits) and symbols cost about one token each."""
    o = ord(c)
    wide = 0x0E00 <= o <= 0x0E7F or 0x3000 <= o <= 0x9FFF or 0xFF00 <= o <= 0xFFEF
    return not wide and (c.isalpha() or c.isspace())


def estimate_tokens(text: str) -> int:
    """A token count that errs high for the models Tamra runs: three letters or spaces of a
    Latin-script text count one; any other character counts one."""
    light = sum(1 for c in text if _is_light(c))
    return len(text) - light + -(-light // 3)


def _cut(text: str, tokens: int) -> str:
    """The longest start of text whose estimate fits in tokens."""
    cost = 0.0
    for i, c in enumerate(text):
        cost += 1 / 3 if _is_light(c) else 1
        if cost > tokens:
            return text[:i]
    return text


def _left(room: int, system: str, question: str) -> int:
    """The tokens room leaves for history beside the system prompt and the question."""
    return room - estimate_tokens(system) - estimate_tokens(question) - PROMPT_MARGIN


def _earlier_answer(answer: str | None) -> str | None:
    """An earlier answer as the prompt repeats it, without markers; None when it is left out.

    Cancelled, failed and search-mode questions have no answer; a not-found reply would only
    teach the model to say "not found" again."""
    text = _SPACED_MARKER.sub("", answer or "").strip()
    return text if text and text not in _NOT_FOUND_REPLIES else None


def context_event(
    messages: list[Message],
    history: Sequence[tuple[str, str | None]],
    sources: list[SourceRecord],
    context_tokens: int,
    reply_tokens: int,
) -> dict:
    """What the prompt puts in the context window, part by part (estimated), what is kept for
    the reply, and how many of the chat's answered exchanges the prompt kept (spec §6)."""

    def tokens(message: Message) -> int:
        return estimate_tokens(message["content"]) + MESSAGE_TOKENS

    source_tokens = estimate_tokens(_blocks(sources)) if sources else 0
    parts = {
        "instructions": tokens(messages[0]) - source_tokens,
        "sources": source_tokens,
        "history": sum(tokens(m) for m in messages[1:-1]),
        "question": tokens(messages[-1]),
    }
    return {
        "type": "context",
        "prompt_tokens": sum(parts.values()),
        "context_tokens": context_tokens,
        "reply_tokens": reply_tokens,
        "parts": parts,
        "source_count": len(sources),
        "history_kept": (len(messages) - 2) // 2,
        "history_total": sum(1 for _, answer in history if _earlier_answer(answer) is not None),
    }


def _with_history(
    system: str, history: Sequence[tuple[str, str | None]], question: str, budget: int
) -> list[Message]:
    """system, the newest earlier exchanges that fit in budget tokens (oldest first), then the
    question. The newest exchange is cut to fit rather than dropped: a follow-up needs it."""
    kept: list[Message] = []
    for earlier_question, earlier_answer in reversed(history):
        answer = _earlier_answer(earlier_answer)
        if answer is None:
            continue
        asked = estimate_tokens(earlier_question) + MESSAGE_TOKENS
        cost = asked + estimate_tokens(answer) + MESSAGE_TOKENS
        if cost > budget:
            room_for_answer = budget - asked - MESSAGE_TOKENS
            if not kept and room_for_answer > 0:
                kept = [
                    {"role": "user", "content": earlier_question},
                    {"role": "assistant", "content": _cut(answer, room_for_answer)},
                ]
            break
        budget -= cost
        kept[:0] = [
            {"role": "user", "content": earlier_question},
            {"role": "assistant", "content": answer},
        ]
    return [{"role": "system", "content": system}, *kept, {"role": "user", "content": question}]


class StaleIndex(Exception):
    """Every project to search was indexed with another embedding model (spec §5)."""

    def __init__(self, names: list[str]):
        super().__init__(", ".join(names))
        self.names = names


class NothingIndexed(Exception):
    """None of the projects to search has an indexed file yet. `skipped` names the stale
    projects that were left out, so the caller can say why."""

    def __init__(self, skipped: list[str]):
        super().__init__("no indexed documents")
        self.skipped = skipped


class AnswerService:
    def __init__(
        self,
        store: Store,
        embed_query: Callable[[str], np.ndarray],
        route: Callable[[], Route],
        *,
        model_id: str,
        settings: AnswerSettings | None = None,
        on_saved: Callable[[int], None] | None = None,
        rerank: Callable[[str, list[str]], np.ndarray | None] | None = None,
    ):
        """on_saved(message_id) runs after a generated answer is saved, on the answering thread:
        it must return quickly (start a thread for slow work). Its failures are only logged.

        rerank(query, texts) scores passages for the query, higher is better; it returns None
        while the reranker is not installed, and the fused order is kept."""
        self._store = store
        self._embed_query = embed_query
        self._rerank = rerank
        self._route = route
        self._model_id = model_id
        self._settings = settings or AnswerSettings()
        self._on_saved = on_saved
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

    def run_if_idle(self, action: Callable[[], None]) -> bool:
        """Run action only if no answer is being written; False (and nothing run) otherwise.

        No answer can start while action runs, so a check followed by the action cannot race.
        """
        with self._state:
            if self._answering:
                return False
            action()
        return True

    def searchable(self, collections: Sequence[Collection]) -> tuple[list[Collection], list[str]]:
        """The projects to search, and the names of the stale ones left out: a project indexed
        with another embedding model is never searched (spec §5, §6).

        Raises StaleIndex when every project is stale, and NothingIndexed when none of the others
        has an indexed file (also when there is no project at all).
        """
        searched = [c for c in collections if c.embedding_model_id == self._model_id]
        skipped = [c.name for c in collections if c not in searched]
        if collections and not searched:
            raise StaleIndex(skipped)
        if not any(self._store.status_counts(c.id)["indexed"] for c in searched):
            raise NothingIndexed(skipped)
        return searched, skipped

    def passages(
        self, collection_ids: Sequence[int], vector: np.ndarray, query: str, k: int
    ) -> list[SourceRecord]:
        """The best k passages of these projects for the query, numbered from 1, best first.
        None when even the best is not close enough: the "not found" gate of spec §6.

        The gate uses the dense similarity of the fused hits; the reranker, when installed, only
        reorders the top candidates."""
        hits = hybrid_search(self._store, collection_ids, vector, fts_query(query))
        if best_similarity(hits) < self._settings.min_similarity:
            return []
        if self._rerank is None:
            chunks = self._store.get_chunks([hit.chunk_id for hit in hits[:k]])
        else:
            n = max(k, self._settings.rerank_candidates)
            chunks = self._reranked(query, self._store.get_chunks([h.chunk_id for h in hits[:n]]))
            chunks = chunks[:k]
        return [
            SourceRecord(n, c.id, c.file_id, c.rel_path, c.text, c.location, c.file_hash)
            for n, c in enumerate(chunks, start=1)
        ]

    def _reranked(self, query: str, chunks: list[ChunkRecord]) -> list[ChunkRecord]:
        """chunks reordered by reranker score, best first; unchanged when the reranker is not
        installed or fails (an answer from the fused order beats no answer)."""
        try:
            scores = self._rerank(query, [c.text for c in chunks]) if chunks else None
        except Exception:
            log.exception("reranking failed; the fused order is kept")
            return chunks
        if scores is None:
            return chunks
        order = sorted(range(len(chunks)), key=lambda i: -scores[i])  # stable: ties keep RRF order
        return [chunks[i] for i in order]

    def ask(
        self,
        chat_id: int,
        question: str,
        *,
        mode: Literal["answer", "search", "chat"] = "answer",
        think: bool = False,
    ) -> Iterator[dict]:
        """Stream the reply as events. "answer" cites the passages found, "search" returns only
        the passages, and "chat" asks the model without searching (its reply has no sources)."""
        if mode not in ("answer", "search", "chat"):
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
            # Stay "answering" until the deferred actions are done: a new answer must not start
            # (and read a provider) while one of them is about to close it.
            while True:
                with self._state:
                    after, self._after = self._after, []
                    if not after:
                        self._answering = False
                        break
                for action in after:
                    _run(action)

    def _ask(self, chat_id: int, question: str, mode: str, think: bool) -> Iterator[dict]:
        if mode == "chat":
            yield from self._chat(chat_id, question, think)
            return
        collections = self._store.list_collections()
        if not collections:
            yield {"type": "error", "message": "Choose a folder of documents first."}
            return
        chat = self._store.get_chat(chat_id)
        if chat is None:
            yield {"type": "error", "message": f"Chat {chat_id} not found."}
            return
        # The chat's scope; an empty one falls back to its home project. A chat with neither
        # searches nothing: it never reaches every project implicitly.
        wanted = chat.scope or ([chat.collection_id] if chat.collection_id is not None else [])
        try:
            searched, skipped = self.searchable([c for c in collections if c.id in wanted])
        except StaleIndex as e:
            yield {
                "type": "error",
                "message": f"The index of {', '.join(e.names)} was built with a different "
                "embedding model. Rebuild the index.",
            }
            return
        except NothingIndexed as e:
            if e.skipped:  # say why those projects were left out, not only that nothing is ready
                yield {"type": "notice", "code": "stale_projects", "names": e.skipped}
            yield {
                "type": "error",
                "message": "No documents are indexed yet. Wait for indexing to finish, "
                "then ask again.",
            }
            return
        route = self._route()
        history = self._store.recent_exchanges(chat_id, MAX_HISTORY_TURNS)
        language = detect_language(question)
        query = query_text(question, history[-1][0] if history else None)
        try:
            vector = self._embed_query(query)
        except Exception as e:
            # Nothing is saved yet: a question that cannot be answered stays out of the chat.
            event = {"type": "error", "message": f"Embedding model unavailable: {e}"}
            if isinstance(e, EmbedderUnavailable):  # missing files: Settings can download them
                event["reason"] = "embedding_missing"
            yield event
            return
        self._store.add_user_message(chat_id, question)
        budget = self._settings.top_k if route.name == "local" else self._settings.api_top_k
        sources = self.passages([c.id for c in searched], vector, query, budget)
        if skipped:
            yield {"type": "notice", "code": "stale_projects", "names": skipped}
        if not sources:
            text = NOT_FOUND[language]
            message_id = self._store.add_assistant_message(
                chat_id, text, provider=None, model=None, sources=[]
            )
            yield {"type": "sources", "sources": []}
            yield {"type": "token", "text": text}
            yield {"type": "done", "message_id": message_id}
            return
        yield {"type": "sources", "sources": source_payloads(self._store, sources)}
        if mode == "search":  # passages only: no model is involved, so none is recorded
            message_id = self._store.add_assistant_message(
                chat_id, "", provider=None, model=None, sources=sources
            )
            yield {"type": "done", "message_id": message_id}
            return
        reply = self._reply_tokens(route, think)
        room = route.context_tokens - reply
        messages = build_messages(question, history, sources, language, room)
        yield context_event(messages, history, sources, route.context_tokens, reply)
        yield from self._write(chat_id, route, messages, sources, think)

    def _reply_tokens(self, route: Route, think: bool) -> int:
        """The part of the context window kept for the reply; the prompt gets the rest. It grows
        by THINKING_HEADROOM where the provider adds it: a local model that thinks, and every
        Anthropic request (tamra.llm.openai_compat, .anthropic_api)."""
        reply = self._settings.max_tokens
        if think or route.name == "anthropic":
            reply += THINKING_HEADROOM
        return reply

    def _chat(self, chat_id: int, question: str, think: bool) -> Iterator[dict]:
        """A chat-mode reply: no project is searched, so none needs to be indexed."""
        if self._store.get_chat(chat_id) is None:
            yield {"type": "error", "message": f"Chat {chat_id} not found."}
            return
        route = self._route()
        history = self._store.recent_exchanges(chat_id, MAX_HISTORY_TURNS)
        reply = self._reply_tokens(route, think)
        room = route.context_tokens - reply
        messages = chat_messages(question, history, detect_language(question), room)
        self._store.add_user_message(chat_id, question)
        yield context_event(messages, history, [], route.context_tokens, reply)
        yield from self._write(chat_id, route, messages, [], think)

    def _write(
        self,
        chat_id: int,
        route: Route,
        messages: list[Message],
        sources: list[SourceRecord],
        think: bool,
    ) -> Iterator[dict]:
        """Stream the model's reply to messages and save it with its sources."""
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
                unsent: Usage | None = None  # the newest counts not yet sent
                sent_at = 0.0
                for chunk in llm.generate(messages, self._settings.max_tokens, think=think):
                    if self._cancel.is_set():
                        cancelled = True
                        break
                    if chunk.kind == "usage":  # the local model's live token counts
                        unsent = chunk.usage
                        if unsent is not None and time.monotonic() - sent_at >= USAGE_INTERVAL:
                            yield _usage_event(unsent)
                            unsent, sent_at = None, time.monotonic()
                        continue
                    if chunk.kind == "thinking":  # shown live, never saved with the answer
                        yield {"type": "thinking", "text": chunk.text}
                        continue
                    parts.append(chunk.text)
                    yield {"type": "token", "text": chunk.text}
                if unsent is not None:  # the final counts
                    yield _usage_event(unsent)
                if not cancelled and not parts:  # e.g. only thinking came back
                    error = "The model returned no answer."
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
                if self._on_saved is not None and sources:
                    _run(lambda: self._on_saved(message_id))
        if error:
            yield {"type": "error", "message": error, "reason": reason}
        if message_id is not None:
            yield {"type": "done", "message_id": message_id}


def _usage_event(usage: Usage) -> dict:
    return {
        "type": "usage",
        "prompt_tokens": usage.prompt_tokens,
        "generated_tokens": usage.generated_tokens,
        "tokens_per_second": round(usage.tokens_per_second, 1),
    }


def _run(action: Callable[[], None]) -> None:
    try:
        action()
    except Exception:  # a failed close must not break the answer or skip the other actions
        log.exception("deferred action failed")
