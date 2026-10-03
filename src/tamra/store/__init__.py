"""Storage: the only module that executes SQL. One SQLite file holds everything (spec §4)."""

from tamra.store.db import capabilities, connect

__all__ = ["capabilities", "connect"]
