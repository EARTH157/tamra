"""Find a passage in a document and show it: PDF page images, or extracted text (spec §8).

Everything here only reads files. Callers pass a folder and a relative path; they never pass a
filesystem path taken from a request.
"""

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pypdfium2 as pdfium

from tamra.ingest.parsers import ParsedDoc, Unit

MIN_PART_CHARS = 12  # a passage part shorter than this is too ambiguous to locate
MIN_SCALE = 0.5
MAX_SCALE = 3.0

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ViewerError(Exception):
    """The requested file or passage cannot be shown."""


def resolve_file(folder: str, rel_path: str) -> Path:
    """The file at folder/rel_path, which must exist and stay inside the collection folder."""
    root = Path(folder).resolve()
    path = (root / rel_path).resolve()
    if path != root and root not in path.parents:
        raise ViewerError("outside the collection folder")
    if not path.is_file():
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


class _Normalized:
    """A unit's text, normalised for matching, with a map back to offsets in the original."""

    def __init__(self, text: str):
        chars: list[str] = []
        origin: list[int] = []
        for i, c in enumerate(text):
            if c.isspace():
                if chars and chars[-1] == " ":
                    continue
                chars.append(" ")
                origin.append(i)
            else:
                for folded in c.casefold():
                    chars.append(folded)
                    origin.append(i)
        self.text = "".join(chars)
        self.origin = origin

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Original offsets for the normalised range [start, end); the range ends on a non-space."""
        return self.origin[start], self.origin[end - 1] + 1


def _normalize(text: str) -> str:
    return _Normalized(text).text.strip()


def _in_hint(unit: Unit, kind: str, hint: dict | None) -> bool:
    if not hint or hint.get("kind") != kind:
        return False
    if kind == "pdf":
        return hint["page_start"] <= unit.page <= hint["page_end"]
    if kind == "docx":
        return hint["paragraph_start"] <= unit.paragraph <= hint["paragraph_end"]
    last_line = unit.line + unit.text.count("\n")
    return unit.line <= hint["line_end"] and hint["line_start"] <= last_line


def locate(doc: ParsedDoc, passage: str, hint: dict | None = None) -> Found | None:
    """Find `passage` (a chunk or a window of one) in the units of `doc`.

    A passage made of `\\n\\n`-separated parts is located by its longest part, then widened over
    the parts before and after it. `hint` is a source location dict; units in its range are
    searched first, so repeated text resolves to the right occurrence.
    """
    parts = [
        p for p in (_normalize(part) for part in passage.replace("\r\n", "\n").split("\n\n")) if p
    ]
    if not parts:
        return None
    anchor_index = max(range(len(parts)), key=lambda i: len(parts[i]))
    if len(parts[anchor_index]) < MIN_PART_CHARS:
        return None

    norms = [_Normalized(unit.text) for unit in doc.units]
    order = [i for i, u in enumerate(doc.units) if _in_hint(u, doc.kind, hint)]
    order += [i for i in range(len(doc.units)) if i not in order]

    anchor = parts[anchor_index]
    for unit_index in order:
        at = norms[unit_index].text.find(anchor)
        if at >= 0:
            break
    else:
        return None

    # (unit index, normalised start, normalised end) of every part that was found
    first = last = (unit_index, at, at + len(anchor))
    # A PDF chunk can run onto the next page, but the box offsets are per page: stay on one.
    reach = range(unit_index, unit_index + 1) if doc.kind == "pdf" else range(len(doc.units))

    cursor = first
    for part in reversed(parts[:anchor_index]):
        found = _search_back(norms, part, cursor[0], cursor[1], reach)
        if found:
            first = cursor = found
    cursor = last
    for part in parts[anchor_index + 1 :]:
        found = _search_forward(norms, part, cursor[0], cursor[2], reach)
        if found:
            last = cursor = found

    return _to_found(doc, norms, first, last)


def _search_back(
    norms: list[_Normalized], part: str, unit: int, before: int, reach: range
) -> tuple[int, int, int] | None:
    if len(part) < MIN_PART_CHARS:
        return None
    for i in range(unit, reach.start - 1, -1):
        if i not in reach:
            continue
        limit = before if i == unit else len(norms[i].text)
        at = norms[i].text.rfind(part, 0, limit)
        if at >= 0:
            return i, at, at + len(part)
    return None


def _search_forward(
    norms: list[_Normalized], part: str, unit: int, after: int, reach: range
) -> tuple[int, int, int] | None:
    if len(part) < MIN_PART_CHARS:
        return None
    for i in range(unit, reach.stop):
        if i not in reach:
            continue
        at = norms[i].text.find(part, after if i == unit else 0)
        if at >= 0:
            return i, at, at + len(part)
    return None


def _to_found(
    doc: ParsedDoc,
    norms: list[_Normalized],
    first: tuple[int, int, int],
    last: tuple[int, int, int],
) -> Found:
    first_unit, last_unit = doc.units[first[0]], doc.units[last[0]]
    start, _ = norms[first[0]].span(first[1], first[2])
    _, end = norms[last[0]].span(last[1], last[2])
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


def pdf_page_count(path: Path) -> int:
    pdf = _open_pdf(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_pdf_page(path: Path, page: int, scale: float) -> bytes:
    """A PNG of the 1-based `page`, at `scale` x 72 dpi (clamped to 0.5..3.0)."""
    scale = min(max(scale, MIN_SCALE), MAX_SCALE)
    pdf = _open_pdf(path)
    try:
        pdf_page = _get_page(pdf, page)
        try:
            bitmap = pdf_page.render(scale=scale)
            try:
                pixels = bitmap.to_numpy()
                rgb = _to_rgb(pixels)
                height, width = rgb.shape[:2]
                return _encode_png(rgb.tobytes(), width, height)
            finally:
                bitmap.close()
        finally:
            pdf_page.close()
    finally:
        pdf.close()


def _to_rgb(pixels):
    """The bitmap as an (h, w, 3) RGB array; pdfium renders BGR, BGRx, BGRA or gray."""
    if pixels.ndim == 2:  # gray
        return np.stack([pixels] * 3, axis=-1)
    return np.ascontiguousarray(pixels[:, :, 2::-1])  # B, G, R(, A) -> R, G, B


def _encode_png(rgb: bytes, width: int, height: int) -> bytes:
    """A minimal 8-bit RGB PNG with filter 0 on every row."""
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))
    return (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 6))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def pdf_rects(
    path: Path, page: int, start: int, end: int
) -> list[tuple[float, float, float, float]]:
    """Boxes covering page text [start, end), as (x0, y0, x1, y1) fractions with a top-left origin.

    The offsets are positions in the page's get_text_range() text, as in `Found`.
    """
    pdf = _open_pdf(path)
    try:
        pdf_page = _get_page(pdf, page)
        try:
            left, bottom, right, top = pdf_page.get_bbox()
            width, height = right - left, top - bottom
            if width <= 0 or height <= 0 or end <= start:
                return []
            textpage = pdf_page.get_textpage()
            try:
                rects = []
                for i in range(textpage.count_rects(start, end - start)):
                    x0, y0, x1, y1 = textpage.get_rect(i)
                    rects.append(
                        (
                            _unit((x0 - left) / width),
                            _unit(1 - (y1 - bottom) / height),
                            _unit((x1 - left) / width),
                            _unit(1 - (y0 - bottom) / height),
                        )
                    )
                return rects
            finally:
                textpage.close()
        finally:
            pdf_page.close()
    finally:
        pdf.close()


def _unit(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _open_pdf(path: Path) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as e:
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
                {
                    "index": unit.paragraph,
                    "text": unit.text,
                    "heading": bool(unit.heading_path)
                    and unit.heading_path[-1] == unit.text.strip(),
                }
                for unit in doc.units
            ],
        }
    raise ViewerError("a PDF is shown as page images")
