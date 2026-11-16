"""Runner protocol for all SQL generation backends."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from cube.engines.base import DatabaseEngine
from cube.types import RunnerRequest, RunnerResult


@runtime_checkable
class Runner(Protocol):
    name: str

    def run(
        self,
        req: RunnerRequest,
        *,
        engine: DatabaseEngine | None = None,
    ) -> RunnerResult: ...
