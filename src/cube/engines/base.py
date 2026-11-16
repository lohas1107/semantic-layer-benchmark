"""DatabaseEngine protocol and unified DatabaseError."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from cube.types import ExecResult


class DatabaseError(RuntimeError):
    """Unified SQL execution error raised by all database engines."""


@runtime_checkable
class DatabaseEngine(Protocol):
    name: str
    dialect: str

    def execute(self, sql: str, *, timeout_s: int = 30, row_cap: int = 100) -> ExecResult: ...

    def ping(self) -> bool: ...

    def close(self) -> None: ...
