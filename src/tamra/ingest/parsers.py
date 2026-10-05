"""Parse PDF, DOCX, TXT, and MD files into text units that remember where they came from."""

import codecs
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import docx
import pypdfium2 as pdfium
from charset_normalizer import from_bytes

SUPPORTED = frozenset({".pdf", ".docx", ".txt", ".md"})

# PDFium is not thread-safe: indexing runs in a worker thread while the viewer serves requests
# on others, so every open-to-close use of a PdfDocument holds this lock.
PDFIUM_LOCK = threading.RLock()

_HEADING = re.compile(r"Heading (\d)")
_THAI_CONSONANTS = range(0x0E01, 0x0E2F)
_THAI_MARKS = frozenset([0x0E31, *range(0x0E34, 0x0E3B), *range(0x0E47, 0x0E4F)])


class ParseError(Exception):
    """The file could not be read as a document of its type."""


@dataclass(frozen=True)
class Unit:
    """A run of document text and the coordinates of its first character.

    pdf: page (1-based) and char (offset of text[0] in the page's extracted text).
    docx: paragraph (0-based index into document.paragraphs), heading_path, and heading_level.
    text: line (1-based number of the line holding text[0]).
    """

    text: str
    page: int = 0
    char: int = 0
    paragraph: int = 0
    heading_path: tuple[str, ...] = ()
    line: int = 0
    heading_level: int = 0  # docx: 1-9 when the paragraph is itself a heading or title


@dataclass(frozen=True)
class ParsedDoc:
    kind: str  # "pdf" | "docx" | "text"
    units: list[Unit]
    note: str | None = None  # shown next to the file, e.g. pages without a text layer


def location(kind: str, first: Unit, first_offset: int, last: Unit, last_offset: int) -> dict:
    """Where the text from first.text[first_offset] to last.text[last_offset - 1] sits."""
    if kind == "pdf":
        return {
            "kind": "pdf",
            "page_start": first.page,
            "char_start": first.char + first_offset,
            "page_end": last.page,
            "char_end": last.char + last_offset,
        }
    if kind == "docx":
        return {
            "kind": "docx",
            "heading_path": list(first.heading_path),
            "paragraph_start": first.paragraph,
            "paragraph_end": last.paragraph,
        }
    return {
        "kind": "text",
        "line_start": first.line + first.text.count("\n", 0, first_offset),
        "line_end": last.line + last.text.count("\n", 0, max(last_offset - 1, 0)),
    }


def parse_file(path: Path) -> ParsedDoc:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix == ".docx":
        return parse_docx(path)
    if suffix in (".txt", ".md"):
        return parse_text(path)
    raise ParseError(f"unsupported file type: {path.suffix}")


def parse_pdf(path: Path) -> ParsedDoc:
    """One unit per page with a text layer; pages without one are listed in the note."""
    with PDFIUM_LOCK:
        return _parse_pdf(path)


def _parse_pdf(path: Path) -> ParsedDoc:
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as e:
        raise ParseError(f"cannot open PDF: {e}") from e
    units: list[Unit] = []
    empty: list[int] = []
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
            finally:
                page.close()
            if text.strip():
                units.append(Unit(text=text, page=index + 1))
            else:
                empty.append(index + 1)
    finally:
        pdf.close()
    note = None
    if empty:
        note = "no text layer on page(s) " + ", ".join(map(str, empty)) + " (needs OCR)"
    return ParsedDoc("pdf", units, note)


def parse_docx(path: Path) -> ParsedDoc:
    """One unit per non-empty paragraph, with the headings above it. Tables are not read."""
    try:
        document = docx.Document(str(path))
    except Exception as e:  # python-docx raises several types for a broken package
        raise ParseError(f"cannot open DOCX: {e}") from e
    units: list[Unit] = []
    headings: list[str] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = paragraph.text
        if not text.strip():
            continue
        style = paragraph.style.name if paragraph.style is not None else ""
        match = _HEADING.fullmatch(style)
        level = int(match.group(1)) if match else (1 if style == "Title" else 0)
        if level:
            headings = headings[: level - 1] + [text.strip()]
        units.append(
            Unit(text=text, paragraph=index, heading_path=tuple(headings), heading_level=level)
        )
    return ParsedDoc("docx", units)


def parse_text(path: Path) -> ParsedDoc:
    """One unit per paragraph (a run of non-blank lines), with its first line number."""
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ParseError(f"cannot read file: {e}") from e
    text = decode_text(data).replace("\r\n", "\n").replace("\r", "\n")
    units: list[Unit] = []
    block: list[str] = []
    start = 0
    for number, line in enumerate(text.split("\n"), start=1):
        if line.strip():
            if not block:
                start = number
            block.append(line)
        elif block:
            units.append(Unit(text="\n".join(block), line=start))
            block = []
    if block:
        units.append(Unit(text="\n".join(block), line=start))
    return ParsedDoc("text", units)


def decode_text(data: bytes) -> str:
    """Decode a text file: BOMs, then UTF-8, then Thai cp874 (TIS-620), then a detected codec."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        thai = data.decode("cp874")
    except UnicodeDecodeError:
        thai = None
    if thai is not None and _reads_as_thai(thai):
        return thai
    best = from_bytes(data).best()
    return str(best) if best is not None else data.decode("latin-1")


def _reads_as_thai(text: str) -> bool:
    """Mostly Thai letters, with vowel and tone marks sitting on consonants.

    Other legacy encodings decoded as cp874 (GBK Chinese, for example) also produce Thai
    letters, but their marks land after arbitrary characters, so the second test fails.
    """
    letters = [c for c in text if c.isalpha()]
    thai = [c for c in letters if 0x0E00 <= ord(c) <= 0x0E7F]
    if not letters or len(thai) < 0.3 * len(letters):
        return False
    marks = placed = 0
    previous = 0
    for c in text:
        code = ord(c)
        if code in _THAI_MARKS:
            marks += 1
            if previous in _THAI_CONSONANTS or previous in _THAI_MARKS:
                placed += 1
        previous = code
    return marks == 0 or placed >= 0.95 * marks
