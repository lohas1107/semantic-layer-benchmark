"""Loader for text_to_sql_benchmark.json into typed records."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Level = Literal["simple", "medium", "complex", "advanced", "expert"]


@dataclass(frozen=True)
class BenchmarkQuestion:
    id: int
    level: Level
    question: str
    expected_sql: str
    tricky_aspects: tuple[str, ...]


def load_benchmark(path: Path) -> list[BenchmarkQuestion]:
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
