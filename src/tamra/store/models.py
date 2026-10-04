from dataclasses import dataclass
from typing import Any

Location = dict[str, Any]


@dataclass(frozen=True)
class Collection:
    id: int
    name: str
    folder_path: str
    embedding_model_id: str
    created_at: str


@dataclass(frozen=True)
class FileRecord:
    id: int
    rel_path: str
    size: int
    mtime: float
    content_hash: str | None
    status: str
    error: str | None
    indexed_at: str | None


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
