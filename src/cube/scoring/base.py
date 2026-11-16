"""Judge protocol for scoring candidate SQL results."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from cube.types import ScoreResult


@runtime_checkable
class Judge(Protocol):
    name: str

    async def score(
        self,
        *,
        question: str,
        schema_text: str,
        expected_sql: str,
        expected_rows: list[list],
        expected_row_count: int,
        candidate_sql: str | None,
        candidate_rows: list[list],
        candidate_row_count: int,
        candidate_error: str | None,
        reference_error: str | None,
    ) -> ScoreResult: ...
