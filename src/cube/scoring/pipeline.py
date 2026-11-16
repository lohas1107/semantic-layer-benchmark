"""Async concurrent scoring pipeline."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

from cube.config import ExperimentSpec
from cube.scoring.base import Judge
from cube.storage.run_store import RunStore
from cube.types import BenchmarkQuestion


async def score_system(
    *,
    spec: ExperimentSpec,
    store: RunStore,
    judge: Judge,
    system_id: str,
    questions: list[BenchmarkQuestion],
    schema_text: str,
    concurrency: int = 5,
    overwrite: bool = False,
) -> tuple[int, int, int]:
    """Score all questions for one system. Returns (passed, failed, excluded)."""
    sys_dir = store.system_dir(system_id)
    out_dir = store.scoring_dir(system_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)

    async def _one(q: BenchmarkQuestion) -> str:
        out_path = store.question_path(out_dir, q.id)
        if out_path.exists() and not overwrite:
            existing = store.load_json(out_path)
            return str(existing.get("analytical_verdict", "fail"))

        ref_path = store.question_path(store.reference_dir, q.id)
        cand_path = store.question_path(sys_dir, q.id)
        ref = store.load_json(ref_path) if ref_path.exists() else {"error": "missing reference"}
        cand = store.load_json(cand_path) if cand_path.exists() else {"error": "missing candidate"}

        cand_err = cand.get("error") or cand.get("claude_error") or cand.get("sql_error")
        async with sem:
            res = await judge.score(
                question=q.question,
                schema_text=schema_text,
                expected_sql=q.expected_sql,
                expected_rows=ref.get("rows") or [],
                expected_row_count=int(ref.get("row_count", 0) or 0),
                candidate_sql=cand.get("sql"),
                candidate_rows=cand.get("rows") or [],
                candidate_row_count=int(cand.get("row_count", 0) or 0),
                candidate_error=cand_err,
                reference_error=ref.get("error"),
            )
        payload = {"id": q.id, "level": q.level, "system": system_id, **asdict(res)}
        store.save_json(out_path, payload)
        return res.analytical_verdict

    verdicts = await asyncio.gather(*(_one(q) for q in questions))
    passed = sum(1 for v in verdicts if v == "pass")
    excluded = sum(1 for v in verdicts if v == "excluded")
    failed = len(verdicts) - passed - excluded
    return passed, failed, excluded
