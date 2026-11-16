"""Shared core types for the cube benchmark."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Level = Literal["simple", "medium", "complex", "advanced", "expert"]
PromptMode = Literal["raw", "semantic"]
Verdict = Literal["pass", "fail", "excluded"]


@dataclass(frozen=True)
class BenchmarkQuestion:
    id: int
    level: Level
    question: str
    expected_sql: str
    tricky_aspects: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecResult:
    rows: list[list[Any]]
    columns: list[str]
    exec_ms: int


@dataclass(frozen=True)
class RunnerRequest:
    question: BenchmarkQuestion
    schema_text: str
    dialect: str
    mode: PromptMode
    semantic_md: str | None = None
    timeout_s: int = 120


@dataclass
class RunnerResult:
    sql: str | None
    raw_response: str
    latency_ms_gen: int
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 1
    error: str | None = None
    infra_error: bool = False
    attempts: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreResult:
    verdict: Verdict
    analytical_verdict: Verdict
    reason: str
    skipped_judge: bool
    judge_tokens_in: int = 0
    judge_tokens_out: int = 0
    judge_latency_ms: int = 0


def load_benchmark(path: Path) -> list[BenchmarkQuestion]:
    """Load benchmark questions JSON into typed BenchmarkQuestion records."""
    data = json.loads(path.read_text(encoding="utf-8"))
    questions_raw = data["questions"]
    out: list[BenchmarkQuestion] = []
    for q in questions_raw:
        level = q["level"]
        if level not in ("simple", "medium", "complex", "advanced", "expert"):
            raise ValueError(f"Unknown level: {level!r} in question {q.get('id')}")
        out.append(
            BenchmarkQuestion(
                id=int(q["id"]),
                level=level,  # type: ignore[arg-type]
                question=q["question"],
                expected_sql=q["expected_sql"],
                tricky_aspects=tuple(q.get("tricky_aspects", [])),
            )
        )
    return out
