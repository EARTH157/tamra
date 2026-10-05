"""Source attribution (spec §7): which saved source passage a selection of an answer came from.

Candidates are only the message's own saved sources, so a match reflects what the model was
given. Each source snapshot is cut into overlapping ~60-token windows, embedded once per
message, and every window is scored against the selection by cosine similarity plus a
character-trigram overlap, which weighs numbers, names and terms that must match exactly.
"""

import hashlib
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
NUMBER_PENALTY = 0.15  # per selection number that another window has and this one lacks
MAX_NUMBER_PENALTIES = 2
STRONG = 0.83
PARTIAL = 0.57

Label = Literal["strong", "partial"]

_MARKER = re.compile(r"\[(\d+)\]")
_MARKER_WITH_SPACE = re.compile(r"\s*\[\d+\]")
_NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
# A sentence ends at . ! ? (when not inside a number such as 1.5), at 。！？ or at a newline;
# [n] markers written right after the end still belong to that sentence. Thai has no full
# stop, so a group of markers followed by whitespace also ends a sentence.
_SENTENCE_END = re.compile(
    r"(?:[.!?]+(?=[\s\[]|$)|[。！？]+|\n)(?:[ \t]*\[\d+\])*|(?:[ \t]*\[\d+\])+(?=\s|$)"
)


def numbers(text: str) -> frozenset[str]:
    """The numbers in the text, normalised so that equal values compare equal.

    Thai digits become ASCII, thousands commas go ("18,500" is "18500", but "1,2,3" is three
    numbers), leading zeros go, and ":" counts as "." with trailing decimal zeros dropped, so
    "7:00", "07.00" and "7" agree and "1.50" equals "1.5". Trigrams skip numbers shorter than
    three characters ("6" or "12" days), yet a number that differs is the commonest way an
    answer misquotes its source.
    """
    found = set()
    for match in _NUMBER.findall(text):
        digits = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in match)
        if _THOUSANDS.fullmatch(digits):
            found.add(_normal(digits.replace(",", "")))
        else:
            found.update(_normal(part) for part in digits.split(","))
    return frozenset(found)


def _normal(number: str) -> str:
    whole, *rest = number.replace(":", ".").split(".")
    whole = whole.lstrip("0") or "0"
    if len(rest) == 1:  # a decimal or a time: "1.50" and "7.00" lose their trailing zeros
        fraction = rest[0].rstrip("0")
        return f"{whole}.{fraction}" if fraction else whole
    return ".".join([whole, *rest])


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
    digest: str  # of the snapshot text: a message id can be reused after a delete


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

    def prepare(self, message: MessageRecord) -> None:
        """Embed and cache the windows of every source, so the first attribute() is fast."""
        self._windows(message.id, list(message.sources))

    def attribute(
        self, message: MessageRecord, selection: str, only: int | None = None
    ) -> list[Match]:
        """Up to three matches; empty means no clear source was found.

        Sources the answer cited for the selection (spec §7.2) come first, then best score first.

        only: consider just that source (an [n] chip was clicked).
        """
        selection = selection.strip()
        candidates = [s for s in message.sources if only is None or s.n == only]
        # The markers only say which sources the answer cited; they are not part of the claim.
        cited = cited_in(message.content, selection)
        # Remove each marker with the space before it, so "allowed [2]." embeds as "allowed.".
        query_text = " ".join(_MARKER_WITH_SPACE.sub("", selection).split())
        if not query_text or not candidates:
            return []
        entries = self._windows(message.id, candidates)
        if not entries:
            return []
        query = np.asarray(self._embed([query_text]), dtype=np.float32)[0]
        query_grams = frozenset(trigrams(query_text))
        query_numbers = numbers(query_text)
        known_numbers = frozenset().union(*(n for e in entries.values() for n in e.numbers))
        # A number that no candidate has cannot be checked against them: never a strong match.
        unchecked = bool(query_numbers - known_numbers)

        scored: list[tuple[bool, float, int, int]] = []  # (not cited, score, source n, window)
        for n, entry in entries.items():
            similarity = entry.vectors @ query
            for index, grams in enumerate(entry.grams):
                overlap = len(query_grams & grams) / max(1, len(query_grams))
                missing = len((query_numbers & known_numbers) - entry.numbers[index])
                penalty = NUMBER_PENALTY * min(missing, MAX_NUMBER_PENALTIES)
                score = float(similarity[index]) + TRIGRAM_WEIGHT * overlap - penalty
                scored.append((n not in cited, score, n, index))
        scored.sort(key=lambda item: (item[0], -item[1], item[2], item[3]))  # cited sources first

        matches: list[Match] = []
        taken: dict[int, list[tuple[int, int]]] = {}
        for _, score, n, index in scored:
            if score < PARTIAL:
                continue
            if len(matches) == MAX_MATCHES:
                break
            start, end = entries[n].ranges[index]
            used = taken.setdefault(n, [])
            if used and (len(entries) > 1 or any(start < b and a < end for a, b in used)):
                continue  # one window per source, unless it is the only one: then no overlaps
            used.append((start, end))
            label = "strong" if score >= STRONG and not unchecked else "partial"
            matches.append(Match(n, start, end, label, score))
        return matches

    def _windows(self, message_id: int, sources: list[SourceRecord]) -> dict[int, _Windows]:
        """The windows of these sources, embedding those not cached (or changed) in one batch."""
        digests = {s.n: hashlib.sha1(s.text.encode("utf-8")).hexdigest() for s in sources}
        with self._lock:
            cached = self._cache.setdefault(message_id, {})
            self._cache.move_to_end(message_id)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            missing = [
                s
                for s in sources
                if s.text.strip() and (s.n not in cached or cached[s.n].digest != digests[s.n])
            ]
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
                        digests[s.n],
                    )
                    row += count
            return {
                s.n: cached[s.n]
                for s in sources
                if s.n in cached and cached[s.n].digest == digests[s.n]
            }
