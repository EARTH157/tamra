"""Find a passage in a document and show it: PDF page images, or extracted text (spec §8).

Everything here only reads files. Callers pass a folder and a relative path; they never pass a
filesystem path taken from a request.
"""

import math
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pypdfium2._helpers.bitmap import PdfPosConv

from tamra.ingest.parsers import PDFIUM_LOCK, ParsedDoc, Unit

MIN_PART_CHARS = 12  # the anchor part must have this many normalised characters
NEAR_CHARS = 40  # a shorter part counts when it sits this close to the part beside it
REACH_UNITS = 3  # a part is searched this many units from the part beside it
MIN_SCALE = 0.5
MAX_SCALE = 3.0
MAX_PIXELS = 25_000_000

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_WHITESPACE = re.compile(r"\s+")


class ViewerError(Exception):
    """The requested file or passage cannot be shown."""


def resolve_file(folder: str, rel_path: str) -> Path:
    """The file at folder/rel_path, which must exist and stay inside the collection folder."""
    try:
        root = Path(folder).resolve()
        path = (root / rel_path).resolve()
    except (OSError, ValueError) as e:  # ValueError: an embedded NUL byte
        raise ViewerError("invalid path") from e
    if not path.is_relative_to(root):
        raise ViewerError("outside the collection folder")
    try:
        is_file = path.is_file()
    except (OSError, ValueError) as e:
        raise ViewerError("invalid path") from e
    if not is_file:
        raise ViewerError("file not found")
    return path


@dataclass(frozen=True)
class Found:
    """Where a passage sits in the current file.

    pdf: `page` (1-based) and `start`/`end` offsets into that page's get_text_range() text.
    docx: `start`/`end` paragraph indices (end exclusive).
    text: `start`/`end` 1-based line numbers (end exclusive).
    """

    kind: Literal["pdf", "docx", "text"]
    page: int | None
    start: int
    end: int


def _fold(text: str) -> str:
    """Whitespace runs become one space and case is folded: the form passages are matched in."""
    return _WHITESPACE.sub(" ", text).casefold()


class _Normalized:
    """A unit's text in `_fold` form, with a map from each character back to the original."""

    def __init__(self, text: str):
        chars: list[str] = []
        origin: list[int] = []

        def add(begin: int, stop: int) -> None:
            for i in range(begin, stop):
                for folded in text[i].casefold():
                    chars.append(folded)
                    origin.append(i)

        position = 0
        for run in _WHITESPACE.finditer(text):
            add(position, run.start())
            chars.append(" ")
            origin.append(run.start())
            position = run.end()
        add(position, len(text))
        self.text = "".join(chars)
        self.origin = origin

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Original offsets for the normalised range [start, end); the range ends on a non-space."""
        return self.origin[start], self.origin[end - 1] + 1


def _in_hint(unit: Unit, kind: str, hint: dict | None) -> bool:
    if not hint or hint.get("kind") != kind:
        return False
    if kind == "pdf":
        return hint["page_start"] <= unit.page <= hint["page_end"]
    if kind == "docx":
        return hint["paragraph_start"] <= unit.paragraph <= hint["paragraph_end"]
    last_line = unit.line + unit.text.count("\n")
    return unit.line <= hint["line_end"] and hint["line_start"] <= last_line


class _Units:
    """Lazy per-unit normalisation: a cheap folded string first, the index map only on a hit."""

    def __init__(self, units: list[Unit]):
        self._units = units
        self._folded: dict[int, str] = {}
        self._full: dict[int, _Normalized] = {}

    def __len__(self) -> int:
        return len(self._units)

    def contains(self, index: int, part: str) -> bool:
        folded = self._folded.get(index)
        if folded is None:
            folded = self._folded[index] = _fold(self._units[index].text)
        return part in folded

    def full(self, index: int) -> _Normalized:
        if index not in self._full:
            self._full[index] = _Normalized(self._units[index].text)
        return self._full[index]


# (unit index, normalised start, normalised end) of a matched part
_Match = tuple[int, int, int]


def locate(doc: ParsedDoc, passage: str, hint: dict | None = None) -> Found | None:
    """Find `passage` (a chunk or a window of one) in the units of `doc`.

    A passage made of `\\n\\n`-separated parts is located by its longest part, then widened over
    the parts before and after it. `hint` is a source location dict; units in its range are
    searched first, so repeated text resolves to the right occurrence.
    """
    parts = [
        p
        for p in (_fold(part).strip() for part in passage.replace("\r\n", "\n").split("\n\n"))
        if p
    ]
    if not parts:
        return None
    anchor_index = max(range(len(parts)), key=lambda i: len(parts[i]))
    anchor = parts[anchor_index]
    if len(anchor) < MIN_PART_CHARS:
        return None

    units = _Units(doc.units)
    hinted = [i for i, u in enumerate(doc.units) if _in_hint(u, doc.kind, hint)]
    hinted_set = set(hinted)
    order = hinted + [i for i in range(len(doc.units)) if i not in hinted_set]
    for unit_index in order:
        if units.contains(unit_index, anchor):
            at = units.full(unit_index).text.find(anchor)
            break
    else:
        return None

    # A PDF chunk can run onto the next page, but box offsets are per page: stay on one.
    spread = 0 if doc.kind == "pdf" else REACH_UNITS

    first = last = (unit_index, at, at + len(anchor))
    cursor = first
    for part in reversed(parts[:anchor_index]):
        found = _search_back(units, part, cursor, spread)
        if found:
            first = cursor = found
    cursor = last
    for part in parts[anchor_index + 1 :]:
        found = _search_forward(units, part, cursor, spread)
        if found:
            last = cursor = found
    return _to_found(doc, units, first, last)


def _search_forward(units: _Units, part: str, cursor: _Match, spread: int) -> _Match | None:
    """The part after `cursor`: ahead within `spread` units if long, else only right beside it."""
    index, _, end = cursor
    short = len(part) < MIN_PART_CHARS
    for i in range(index, min(index + spread, len(units) - 1) + 1):
        if short and i > index + 1:
            return None  # only the next unit's opening counts
        if not units.contains(i, part):
            if short and i > index:
                return None
            continue
        text = units.full(i).text
        begin = end if i == index else 0
        stop = begin + NEAR_CHARS if short else len(text)
        at = text.find(part, begin, stop)
        if at >= 0:
            return i, at, at + len(part)
        if short and i > index:
            return None
    return None


def _search_back(units: _Units, part: str, cursor: _Match, spread: int) -> _Match | None:
    """The part before `cursor`: behind within `spread` units if long, else only right beside it."""
    index, start, _ = cursor
    short = len(part) < MIN_PART_CHARS
    for i in range(index, max(index - spread, 0) - 1, -1):
        if short and i < index - 1:
            return None  # only the previous unit's closing counts
        if not units.contains(i, part):
            if short and i < index:
                return None
            continue
        text = units.full(i).text
        stop = start if i == index else len(text)
        begin = max(stop - NEAR_CHARS, 0) if short else 0
        at = text.rfind(part, begin, stop)
        if at >= 0:
            return i, at, at + len(part)
        if short and i < index:
            return None
    return None


def _to_found(doc: ParsedDoc, units: _Units, first: _Match, last: _Match) -> Found:
    first_unit, last_unit = doc.units[first[0]], doc.units[last[0]]
    start, _ = units.full(first[0]).span(first[1], first[2])
    _, end = units.full(last[0]).span(last[1], last[2])
    if doc.kind == "pdf":
        return Found("pdf", first_unit.page, first_unit.char + start, first_unit.char + end)
    if doc.kind == "docx":
        return Found("docx", None, first_unit.paragraph, last_unit.paragraph + 1)
    return Found(
        "text",
        None,
        first_unit.line + first_unit.text.count("\n", 0, start),
        last_unit.line + last_unit.text.count("\n", 0, end - 1) + 1,
    )


# PDFium is not thread-safe, so each function below holds PDFIUM_LOCK from open to close.


def pdf_page_count(path: Path) -> int:
    with PDFIUM_LOCK:
        pdf = _open_pdf(path)
        try:
            return len(pdf)
        finally:
            pdf.close()


def render_pdf_page(path: Path, page: int, scale: float) -> bytes:
    """A PNG of the 1-based `page`, at `scale` x 72 dpi (clamped to 0.5..3.0, at most 25 MP)."""
    scale = min(max(scale, MIN_SCALE), MAX_SCALE) if math.isfinite(scale) else 1.0
    with PDFIUM_LOCK:
        pdf = _open_pdf(path)
        try:
            pdf_page = _get_page(pdf, page)
            try:
                width, height = pdf_page.get_size()
                if width * height * scale * scale > MAX_PIXELS:
                    scale = 0.999 * math.sqrt(MAX_PIXELS / (width * height))  # sizes round up
                bitmap = pdf_page.render(scale=scale)
                try:
                    return _encode_png(_to_rgb(bitmap.to_numpy()))
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        finally:
            pdf.close()


def _to_rgb(pixels: np.ndarray) -> np.ndarray:
    """The bitmap as an (h, w, 3) RGB array; pdfium renders BGR, BGRx, BGRA or gray."""
    if pixels.ndim == 2:  # gray
        return np.stack([pixels] * 3, axis=-1)
    return np.ascontiguousarray(pixels[:, :, 2::-1])  # B, G, R(, A) -> R, G, B


def _encode_png(rgb: np.ndarray) -> bytes:
    """A minimal 8-bit RGB PNG from an (h, w, 3) uint8 array, with filter 0 on every row."""
    height, width = rgb.shape[:2]
    rows = np.zeros((height, 1 + width * 3), dtype=np.uint8)
    rows[:, 1:] = rgb.reshape(height, width * 3)
    return (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


_DEVICE_UNITS = 1000  # device pixels per point: pdfium maps to integers, so ask for fine ones


def pdf_rects(
    path: Path, page: int, start: int, end: int
) -> list[tuple[float, float, float, float]]:
    """Boxes covering page text [start, end), as (x0, y0, x1, y1) fractions with a top-left origin.

    The offsets are positions in the page's get_text_range() text, as in `Found`. The boxes are
    where the text appears in the rendered page, so page rotation and the crop box are applied.
    """
    if end <= start:
        return []
    with PDFIUM_LOCK:
        pdf = _open_pdf(path)
        try:
            pdf_page = _get_page(pdf, page)
            try:
                return _page_rects(pdf_page, start, end)
            finally:
                pdf_page.close()
        finally:
            pdf.close()


def _page_rects(pdf_page, start: int, end: int) -> list[tuple[float, float, float, float]]:
    width, height = pdf_page.get_size()  # as rendered: rotation and crop box applied
    if width <= 0 or height <= 0:
        return []
    device_w, device_h = round(width * _DEVICE_UNITS), round(height * _DEVICE_UNITS)
    to_device = PdfPosConv(pdf_page, (0, 0, device_w, device_h, 0))
    textpage = pdf_page.get_textpage()
    try:
        first, after = _char_range(textpage, start, end)
        rects = []
        for i in range(textpage.count_rects(first, after - first)):
            left, bottom, right, top = textpage.get_rect(i)
            corners = [to_device.to_bitmap(x, y) for x in (left, right) for y in (bottom, top)]
            xs, ys = [c[0] for c in corners], [c[1] for c in corners]
            rects.append(
                (
                    _unit(min(xs) / device_w),
                    _unit(min(ys) / device_h),
                    _unit(max(xs) / device_w),
                    _unit(max(ys) / device_h),
                )
            )
        return rects
    finally:
        textpage.close()


def _char_range(textpage, start: int, end: int) -> tuple[int, int]:
    """pdfium char indices (first, after-last) for text offsets [start, end).

    Offsets count Python characters in get_text_range(); pdfium counts UTF-16 units (an emoji is
    two) and leaves some characters out of the text, so both differences are mapped here.
    """
    text = textpage.get_text_range()
    start, end = min(max(start, 0), len(text)), min(max(end, 0), len(text))
    units_before = start + sum(ord(c) > 0xFFFF for c in text[:start])
    units_through = end + sum(ord(c) > 0xFFFF for c in text[:end])
    first = pdfium_c.FPDFText_GetCharIndexFromTextIndex(textpage, units_before)
    last = pdfium_c.FPDFText_GetCharIndexFromTextIndex(textpage, max(units_through - 1, 0))
    if first < 0:
        first = units_before
    if last < 0:
        last = units_through - 1
    return first, max(last + 1, first)


def _unit(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _open_pdf(path: Path) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(path)
    except (pdfium.PdfiumError, OSError) as e:
        raise ViewerError(f"cannot open PDF: {e}") from e


def _get_page(pdf: pdfium.PdfDocument, page: int):
    if not 1 <= page <= len(pdf):
        raise ViewerError("page out of range")
    return pdf[page - 1]


def text_view(doc: ParsedDoc) -> dict:
    """The extracted text of a DOCX or text file, for display with a highlighted range."""
    if doc.kind == "text":
        lines: list[str] = []
        for unit in doc.units:
            lines.extend([""] * (unit.line - 1 - len(lines)))  # blank lines between paragraphs
            lines.extend(unit.text.split("\n"))
        return {"kind": "text", "lines": lines}
    if doc.kind == "docx":
        return {
            "kind": "docx",
            "paragraphs": [
                {"index": u.paragraph, "text": u.text, "heading": u.heading_level > 0}
                for u in doc.units
            ],
        }
    raise ViewerError("a PDF is shown as page images")
