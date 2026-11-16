"""Score every (question, system) pair using the Sonnet judge. 5-way concurrent."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio
import json

from rich.console import Console
from rich.progress import Progress

from benchmarks.text_to_sql.benchmark_data import load_benchmark
from benchmarks.text_to_sql.engines import ENGINES, get_engine
from benchmarks.text_to_sql.paths import (
    ensure,
    question_path,
    reference_dir,
    run_dir,
    scoring_dir,
)
from benchmarks.text_to_sql.scorer import ScoreResult, score_one

MAX_CONCURRENT = 5
NON_SYSTEM_DIRS = {"reference", "scoring"}


def discover_systems(run_id: str, *, root: Path | None = None) -> list[str]:
    """Every runs/<run_id>/<dir> that holds per-question outputs is a system."""
    base = run_dir(run_id, root=root)
    if not base.exists():
        return []
    return sorted(
        d.name
        for d in base.iterdir()
        if d.is_dir() and d.name not in NON_SYSTEM_DIRS and any(d.glob("q*.json"))
    )


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def _score_one_async(
    sem: asyncio.Semaphore,
    *,
    question: str,
    schema_text: str,
    reference: dict,
    candidate: dict,
) -> ScoreResult:
    async with sem:
        return await asyncio.to_thread(
            score_one,
            question=question,
            schema_text=schema_text,
            reference=reference,
            candidate=candidate,
            candidate_sql_key="sql",
        )


async def main_async(run_id: str, engine_name: str = "clickhouse") -> int:
    console = Console()
    engine = get_engine(engine_name)
    if not engine.schema_fixture.exists():
        console.print(f"[red]{engine.schema_fixture} missing.[/red]")
        return 1
    schema_text = engine.schema_fixture.read_text(encoding="utf-8")
    questions = load_benchmark(engine.benchmark_json)
    ref_dir = reference_dir(run_id)

    tasks: list[tuple[str, int, asyncio.Task]] = []
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    for system in discover_systems(run_id):
        sys_dir = run_dir(run_id) / system
        out_dir = ensure(scoring_dir(run_id, system))
        for q in questions:
            out_path = question_path(out_dir, q.id)
            if out_path.exists():
                continue
            ref_path = question_path(ref_dir, q.id)
            cand_path = question_path(sys_dir, q.id)
            if not ref_path.exists() or not cand_path.exists():
                skeleton = {
                    "question_id": q.id,
                    "system": system,
                    "verdict": "fail",
                    "analytical_verdict": "fail",
                    "reason": "missing_input",
                    "skipped_judge": True,
                    "judge_tokens_in": 0,
                    "judge_tokens_out": 0,
                    "judge_latency_ms": 0,
                }
                out_path.write_text(json.dumps(skeleton, indent=2), encoding="utf-8")
                continue
            reference = _load_json(ref_path)
            candidate = _load_json(cand_path)
            tasks.append(
                (
                    system,
                    q.id,
                    asyncio.create_task(
                        _score_one_async(
                            sem,
                            question=q.question,
                            schema_text=schema_text,
                            reference=reference,
                            candidate=candidate,
                        )
                    ),
                )
            )

    with Progress() as progress:
        prog = progress.add_task("scoring", total=len(tasks))
        for system, qid, task in tasks:
            result: ScoreResult = await task
            rec = {
                "question_id": qid,
                "system": system,
                "verdict": result.verdict,
                "analytical_verdict": result.analytical_verdict,
                "reason": result.reason,
                "skipped_judge": result.skipped_judge,
                "judge_tokens_in": result.judge_tokens_in,
                "judge_tokens_out": result.judge_tokens_out,
                "judge_latency_ms": result.judge_latency_ms,
            }
            out_path = question_path(scoring_dir(run_id, system), qid)
            out_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
            progress.advance(prog)

    console.print(f"[green]Scoring done for run {run_id}[/green]")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--engine", choices=sorted(ENGINES), default="clickhouse",
                        help="Selects the question file and DDL shown to the judge")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args.run_id, args.engine))


if __name__ == "__main__":
    raise SystemExit(main())
