from dataclasses import dataclass, field
from typing import Any

Location = dict[str, Any]


@dataclass(frozen=True)
class Collection:
    id: int
    name: str
    folder_path: str
    embedding_model_id: str
    created_at: str
    notes: str = ""  # the project's notes for the model (project notes spec); "" when none


@dataclass(frozen=True)
class FileRecord:
    id: int
    collection_id: int
    rel_path: str
    size: int
    mtime: float
    content_hash: str | None
    status: str
    error: str | None
    indexed_at: str | None
    needs_ocr: bool = False  # the last parse left pages for OCR: the OCR models were missing


@dataclass(frozen=True)
class OcrPage:
    """A PDF page read by OCR: its text, and its character boxes as 4 little-endian float32
    per code point of `text` (x0, y0, x1, y1 as page fractions, top-left origin; a newline's
    box is zeros)."""

    page: int
    text: str
    boxes: bytes


@dataclass(frozen=True)
class ChunkInput:
    text: str
    location: Location


@dataclass(frozen=True)
class ChunkRecord:
    id: int
    file_id: int
    rel_path: str
    ord: int
    text: str
    location: Location
    file_hash: str | None


@dataclass(frozen=True)
class Chat:
    id: int
    title: str
    created_at: str
    updated_at: str
    collection_id: int | None = None  # the home project; None until the chat has one
    scope: list[int] = field(default_factory=list)  # the projects a question searches


@dataclass(frozen=True)
class SourceRecord:
    n: int
    chunk_id: int | None
    file_id: int | None
    rel_path: str
    text: str
    location: Location
    file_hash: str | None


@dataclass(frozen=True)
class MessageRecord:
    id: int
    role: str
    content: str
    provider: str | None
    model: str | None
    created_at: str
    sources: tuple[SourceRecord, ...] = ()
