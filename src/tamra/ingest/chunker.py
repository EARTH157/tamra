"""Split parsed documents into ~450-token chunks with ~15% overlap, keeping locations (spec §5)."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tokenizers import Tokenizer

from tamra.ingest.parsers import ParsedDoc, Unit, location
from tamra.store import ChunkInput

TokenSpans = Callable[[str], list[tuple[int, int]]]

CHUNK_TOKENS = 450
OVERLAP_TOKENS = 68  # about 15%

# Where to split a unit that is too long, coarsest first; a split happens at the end of a match.
_SPLITTERS = (
    re.compile(r"\n[ \t]*\n\s*"),  # blank line
    re.compile(r"[.!?]+\s+|[。！？]+"),  # sentence end
    re.compile(r"\n"),  # line break
    re.compile(r"\s+"),  # whitespace (Thai separates phrases with spaces)
)


@dataclass(frozen=True)
class _Piece:
    unit: Unit
    start: int
    end: int
    tokens: int


def bge_token_spans(tokenizer_path: Path) -> TokenSpans:
    """Token spans from the bge-m3 tokenizer, with no truncation and no padding."""
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.no_truncation()
    tokenizer.no_padding()

    def spans(text: str) -> list[tuple[int, int]]:
        return tokenizer.encode(text, add_special_tokens=False).offsets

    return spans


def chunk_document(
    doc: ParsedDoc,
    spans: TokenSpans,
    max_tokens: int = CHUNK_TOKENS,
    overlap: int = OVERLAP_TOKENS,
) -> list[ChunkInput]:
    pieces = [
        _Piece(unit, start, end, tokens)
        for unit in doc.units
        for start, end, tokens in _split(unit.text, 0, len(unit.text), spans, max_tokens, 0)
        if tokens > 0
    ]
    chunks: list[ChunkInput] = []
    current: list[_Piece] = []
    size = 0
    for piece in pieces:
        if current and size + piece.tokens > max_tokens:
            chunks.append(_make_chunk(doc.kind, current))
            current, size = _tail(current, overlap)
            while current and size + piece.tokens > max_tokens:
                size -= current.pop(0).tokens
        current.append(piece)
        size += piece.tokens
    if current:
        chunks.append(_make_chunk(doc.kind, current))
    return chunks


def _split(
    text: str, start: int, end: int, spans: TokenSpans, limit: int, level: int
) -> list[tuple[int, int, int]]:
    """Cut text[start:end] into (start, end, tokens) pieces of at most `limit` tokens."""
    offsets = spans(text[start:end])
    if len(offsets) <= limit:
        return [(start, end, len(offsets))]
    if level < len(_SPLITTERS):
        cuts = [m.end() for m in _SPLITTERS[level].finditer(text, start, end)]
        bounds = sorted({start, end, *(c for c in cuts if start < c < end)})
        if len(bounds) > 2:
            return [
                piece
                for a, b in zip(bounds, bounds[1:], strict=False)
                for piece in _split(text, a, b, spans, limit, level + 1)
            ]
        return _split(text, start, end, spans, limit, level + 1)
    pieces: list[tuple[int, int]] = []  # no separator left: cut at token boundaries
    piece_start = start
    for i in range(limit, len(offsets), limit):
        cut = start + offsets[i - 1][1]
        if cut > piece_start:
            pieces.append((piece_start, cut))
            piece_start = cut
    pieces.append((piece_start, end))
    return [(a, b, len(spans(text[a:b]))) for a, b in pieces]


def _tail(pieces: list[_Piece], overlap: int) -> tuple[list[_Piece], int]:
    """The trailing pieces that fit in the overlap budget; they open the next chunk."""
    kept: list[_Piece] = []
    size = 0
    for piece in reversed(pieces):
        if size + piece.tokens > overlap:
            break
        kept.insert(0, piece)
        size += piece.tokens
    return kept, size


def _make_chunk(kind: str, pieces: list[_Piece]) -> ChunkInput:
    parts: list[str] = []
    previous: _Piece | None = None
    for piece in pieces:
        if previous is not None and not (
            previous.unit is piece.unit and previous.end == piece.start
        ):
            parts.append("\n\n")
        parts.append(piece.unit.text[piece.start : piece.end])
        previous = piece
    text = "".join(parts).replace("\r\n", "\n").strip()
    first, last = pieces[0], pieces[-1]
    # Splits leave separators at piece edges and the text is stripped, so trim the range alike.
    first_text = first.unit.text[first.start : first.end]
    last_text = last.unit.text[last.start : last.end]
    start = first.start + len(first_text) - len(first_text.lstrip())
    end = last.end - (len(last_text) - len(last_text.rstrip()))
    return ChunkInput(text, location(kind, first.unit, start, last.unit, end))
