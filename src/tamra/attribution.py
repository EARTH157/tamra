"""Source attribution (spec §7): which saved source passage a selection of an answer came from.

Candidates are only the message's own saved sources, so a match reflects what the model was
given. Each source snapshot is cut into overlapping ~60-token windows, embedded once per
message, and every window is scored against the selection by cosine similarity plus a
character-trigram overlap, which weighs numbers, names and terms that must match exactly.
"""

import re
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np

from tamra.ingest.chunker import TokenSpans
from tamra.retriever import trigrams
from tamra.store import MessageRecord, SourceRecord

WINDOW_TOKENS = 60
WINDOW_STRIDE = 30  # 50% overlap
MAX_MATCHES = 3

# Calibrated on eval/attribution.jsonl with bge-m3 (scripts/eval_attribution.py).
TRIGRAM_WEIGHT = 0.2
CITED_BONUS = 0.05
NUMBER_PENALTY = 0.15  # per number of the selection that the window lacks
MAX_NUMBER_PENALTIES = 2
STRONG = 0.83
PARTIAL = 0.57

Label = Literal["strong", "partial"]

_MARKER = re.compile(r"\[(\d+)\]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# A sentence ends at . ! ? (when not inside a number such as 1.5), at 。！？ or at a newline;
# [n] markers written right after the end still belong to that sentence.
_SENTENCE_END = re.compile(r"(?:[.!?]+(?=[\s\[]|$)|[。！？]+|\n)(?:[ \t]*\[\d+\])*")


def numbers(text: str) -> frozenset[str]:
    """The numbers in the text with digits normalised and thousands commas removed.

    Trigrams skip numbers shorter than three characters ("6" or "12" days), yet a number that
    differs is the commonest way an answer misquotes its source.
    """
    found = set()
    for match in _NUMBER.findall(text):
        digits = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in match)
        found.add(digits.replace(",", ""))
    return frozenset(found)


@dataclass(frozen=True)
class Match:
    n: int
    start: int  # the matched window's character range in the source snapshot
    end: int
    label: Label
    score: float  # internal: never shown to the user


def windows(text: str, spans: TokenSpans) -> list[tuple[int, int]]:
    """Character ranges of ~60-token windows with 50% overlap that together cover the text.

    A text of 60 tokens or fewer is one window.
    """
    offsets = spans(text)
    count = len(offsets)
    if count <= WINDOW_TOKENS:
        return [(0, len(text))]
    ranges: list[tuple[int, int]] = []
    for first in range(0, count, WINDOW_STRIDE):
        last = min(first + WINDOW_TOKENS, count)
        start = 0 if first == 0 else offsets[first][0]
        end = len(text) if last == count else offsets[last - 1][1]
        ranges.append((start, end))
        if last == count:
            break
    return ranges


def cited_in(content: str, selection: str) -> set[int]:
    """The [n] markers inside the selection, or inside the sentence(s) of content that hold it."""
    cited = {int(n) for n in _MARKER.findall(selection)}
    position = content.find(selection) if selection else -1
    if position < 0:
        return cited
    start, end = position, position + len(selection)
    bounds = [0, *(m.end() for m in _SENTENCE_END.finditer(content)), len(content)]
    touched = [(a, b) for a, b in zip(bounds, bounds[1:], strict=False) if a < end and b > start]
    if touched:
        cited.update(int(n) for n in _MARKER.findall(content[touched[0][0] : touched[-1][1]]))
    return cited


@dataclass(frozen=True)
class _Windows:
    ranges: list[tuple[int, int]]
    vectors: np.ndarray  # one L2-normalised row per window
    grams: list[frozenset[str]]
    numbers: list[frozenset[str]]


class Attributor:
    """Matches a selection to the windows of a message's saved sources. Thread-safe."""

    def __init__(
        self,
        embed: Callable[[list[str]], np.ndarray],
        spans: TokenSpans,
        cache_size: int = 16,
    ):
        """embed: texts to L2-normalised rows; spans: token spans of the bge tokenizer."""
        self._embed = embed
        self._spans = spans
        self._cache_size = max(1, cache_size)
        self._cache: OrderedDict[int, dict[int, _Windows]] = OrderedDict()  # LRU over messages
        self._lock = threading.Lock()

    def attribute(
        self, message: MessageRecord, selection: str, only: int | None = None
    ) -> list[Match]:
        """Up to three matches, best first; empty means no clear source was found.

        only: consider just that source (an [n] chip was clicked).
        """
        selection = selection.strip()
        candidates = [s for s in message.sources if only is None or s.n == only]
        if not selection or not candidates:
            return []
        entries = self._windows(message.id, candidates)
        if not entries:
            return []
        query = np.asarray(self._embed([selection]), dtype=np.float32)[0]
        selection_grams = frozenset(trigrams(selection))
        selection_numbers = numbers(selection)
        cited = cited_in(message.content, selection)

        scored: list[tuple[float, int, int]] = []  # (score, source n, window index)
        for n, entry in entries.items():
            similarity = entry.vectors @ query
            bonus = CITED_BONUS if n in cited else 0.0
            for index, grams in enumerate(entry.grams):
                overlap = len(selection_grams & grams) / max(1, len(selection_grams))
                missing = len(selection_numbers - entry.numbers[index])
                penalty = NUMBER_PENALTY * min(missing, MAX_NUMBER_PENALTIES)
                score = float(similarity[index]) + TRIGRAM_WEIGHT * overlap + bonus - penalty
                scored.append((score, n, index))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))

        matches: list[Match] = []
        taken: dict[int, list[tuple[int, int]]] = {}
        for score, n, index in scored:
            if score < PARTIAL or len(matches) == MAX_MATCHES:
                break
            start, end = entries[n].ranges[index]
            used = taken.setdefault(n, [])
            if used and (len(entries) > 1 or any(start < b and a < end for a, b in used)):
                continue  # one window per source, unless it is the only one: then no overlaps
            used.append((start, end))
            matches.append(Match(n, start, end, "strong" if score >= STRONG else "partial", score))
        return matches

    def _windows(self, message_id: int, sources: list[SourceRecord]) -> dict[int, _Windows]:
        """The windows of these sources, embedding those not cached yet in one batch."""
        with self._lock:
            cached = self._cache.setdefault(message_id, {})
            self._cache.move_to_end(message_id)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            missing = [s for s in sources if s.n not in cached and s.text.strip()]
            if missing:
                ranges = {s.n: windows(s.text, self._spans) for s in missing}
                texts = [s.text[a:b] for s in missing for a, b in ranges[s.n]]
                vectors = np.asarray(self._embed(texts), dtype=np.float32)
                row = 0
                for s in missing:
                    count = len(ranges[s.n])
                    cached[s.n] = _Windows(
                        ranges[s.n],
                        vectors[row : row + count],
                        [frozenset(trigrams(t)) for t in texts[row : row + count]],
                        [numbers(t) for t in texts[row : row + count]],
                    )
                    row += count
            return {s.n: cached[s.n] for s in sources if s.n in cached}
