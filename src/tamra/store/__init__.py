"""Storage: the only module that executes SQL. One SQLite file holds everything (spec §4)."""

from tamra.store.db import capabilities, connect
from tamra.store.models import (
    Chat,
    ChunkInput,
    ChunkRecord,
    Collection,
    FileRecord,
    MessageRecord,
    OcrPage,
    SourceRecord,
)
from tamra.store.repo import FILE_STATUSES, NameTaken, Store

__all__ = [
    "FILE_STATUSES",
    "Chat",
    "ChunkInput",
    "ChunkRecord",
    "Collection",
    "FileRecord",
    "MessageRecord",
    "NameTaken",
    "OcrPage",
    "SourceRecord",
    "Store",
    "capabilities",
    "connect",
]
