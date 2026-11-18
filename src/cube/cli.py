"""Unified CLI for the cube benchmark."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import subprocess
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from cube.config import REPO_ROOT, ExperimentSpec, SystemConfig, load_experiment
from cube.engines import DatabaseEngine, DatabaseError, create_engine
from cube.reporting import print_report
from cube.runners import create_runner
from cube.scoring import LLMJudge, score_system
from cube.storage import RunStore
from cube.types import BenchmarkQuestion, RunnerRequest, load_benchmark

console = Console()
_ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


@contextlib.contextmanager
def _lifecycle_guard(spec: ExperimentSpec, *, skip: bool = False) -> Iterator[None]:
    """Run optional before_run / after_run hooks around the model-run phase."""
    if skip or (not spec.lifecycle.before_run and not spec.lifecycle.after_run):
        yield
        return

    if spec.lifecycle.before_run:
        console.print(f"[cyan]lifecycle.before_run:[/cyan] {spec.lifecycle.before_run}")
        proc = subprocess.run(
            spec.lifecycle.before_run,
            shell=True,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            text=True,
            check=True,
        )
        for line in (proc.stdout or "").splitlines():
            m = _ENV_LINE_RE.match(line.strip())
            if m:
                os.environ[m.group(1)] = m.group(2)
            elif line.strip():
                console.print(line)

    try:
        yield
    finally:
        if spec.lifecycle.after_run:
            console.print(f"[cyan]lifecycle.after_run:[/cyan] {spec.lifecycle.after_run}")
            subprocess.run(
                spec.lifecycle.after_run,
                shell=True,
                cwd=REPO_ROOT,
                check=False,
            )


def _filter_questions(
    questions: list[BenchmarkQuestion],
    spec: ExperimentSpec,
    *,
    only: str | None = None,
    limit: int | None = None,
) -> list[BenchmarkQuestion]:
    only_ids: set[int] | None = None
    if only:
        only_ids = {int(x.strip()) for x in only.split(",") if x.strip()}
    elif spec.dataset.only_ids:
        only_ids = set(spec.dataset.only_ids)

    filtered = [q for q in questions if only_ids is None or q.id in only_ids]
    eff_limit = limit if limit is not None else spec.dataset.limit
    if eff_limit is not None:
        filtered = filtered[:eff_limit]
    return filtered


def _default_run_id(spec: ExperimentSpec) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{spec.experiment.name}-{ts}"


def _run_reference(
    spec: ExperimentSpec,
    store: RunStore,
    questions: list[BenchmarkQuestion],
    *,
    overwrite: bool = False,
) -> None:
    engine = create_engine(spec.engine)
    store.ensure_dirs()
    try:
        for q in questions:
            out_path = store.question_path(store.reference_dir, q.id)
            if out_path.exists() and not overwrite:
                continue
            payload: dict[str, Any] = {
                "id": q.id,
                "level": q.level,
                "expected_sql": q.expected_sql,
            }
            try:
                res = engine.execute(
                    q.expected_sql,
                    timeout_s=spec.engine.exec_timeout_s,
                    row_cap=spec.engine.row_cap,
                )
                payload.update(
                    {
                        "columns": res.columns,
                        "rows": res.rows,
                        "row_count": len(res.rows),
                        "exec_ms": res.exec_ms,
                        "error": None,
                    }
                )
            except DatabaseError as exc:
                payload.update(
                    {
                        "columns": [],
                        "rows": [],
                        "row_count": 0,
                        "exec_ms": 0,
                        "error": str(exc),
                    }
                )
            store.save_json(out_path, payload)
    finally:
        engine.close()


def _run_one_question(
    *,
    q: BenchmarkQuestion,
    spec: ExperimentSpec,
    sys_cfg: SystemConfig,
    runner: Any,
    engine: DatabaseEngine,
    schema_text: str,
    semantic_md: str | None,
) -> dict[str, Any]:
    timeout_s = int(sys_cfg.params.get("timeout_s", 120))
    req = RunnerRequest(
        question=q,
        schema_text=schema_text,
        dialect=engine.dialect,
        mode=sys_cfg.mode,
        semantic_md=semantic_md if sys_cfg.mode == "semantic" else None,
        timeout_s=timeout_s,
    )
    gen = runner.run(req, engine=engine)
    payload: dict[str, Any] = {
        "id": q.id,
        "level": q.level,
        "system": sys_cfg.id,
        "runner": sys_cfg.runner,
        "model": sys_cfg.model,
        "mode": sys_cfg.mode,
        "sql": gen.sql,
        "raw_response": gen.raw_response,
        "latency_ms_gen": gen.latency_ms_gen,
        "tokens_in": gen.tokens_in,
        "tokens_out": gen.tokens_out,
        "tool_calls": gen.tool_calls,
        "rounds": gen.rounds,
        "attempts": gen.attempts,
        "infra_error": gen.infra_error,
        "error": gen.error,
        "sql_error": None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "exec_ms": 0,
    }
    if gen.sql and not gen.error:
        try:
            exec_res = engine.execute(
                gen.sql,
                timeout_s=spec.engine.exec_timeout_s,
                row_cap=spec.engine.row_cap,
            )
            payload.update(
                {
                    "columns": exec_res.columns,
                    "rows": exec_res.rows,
                    "row_count": len(exec_res.rows),
                    "exec_ms": exec_res.exec_ms,
                }
            )
        except DatabaseError as exc:
            payload["sql_error"] = str(exc)
    return payload


def _run_systems(
    spec: ExperimentSpec,
    store: RunStore,
    questions: list[BenchmarkQuestion],
    *,
    system_filter: str | None = None,
    overwrite: bool = False,
) -> None:
    schema_text = spec.dataset.schema_path.read_text(encoding="utf-8")
    semantic_md = (
        spec.dataset.semantic_layer.read_text(encoding="utf-8")
        if spec.dataset.semantic_layer and spec.dataset.semantic_layer.exists()
        else None
    )
    systems = [
        s for s in spec.systems if system_filter is None or s.id == system_filter
    ]
    if not systems:
        raise ValueError(f"No matching system {system_filter!r} in experiment")

    engines: dict[str, DatabaseEngine] = {}

    def _get_engine(dsn_env: str) -> DatabaseEngine:
        if dsn_env not in engines:
            cfg = spec.engine.model_copy(update={"dsn_env": dsn_env})
            engines[dsn_env] = create_engine(cfg)
        return engines[dsn_env]

    try:
        for sys_cfg in systems:
            runner = create_runner(sys_cfg)
            engine = _get_engine(sys_cfg.dsn_env or spec.engine.dsn_env)
            sys_dir = store.system_dir(sys_cfg.id)
            pending = [
                q
                for q in questions
                if overwrite or not store.question_path(sys_dir, q.id).exists()
            ]
            console.print(
                f"[cyan]Running {sys_cfg.display_label}[/cyan] "
                f"({len(pending)}/{len(questions)} pending, concurrency={sys_cfg.concurrency})"
            )
            if not pending:
                continue
            with ThreadPoolExecutor(max_workers=max(1, sys_cfg.concurrency)) as pool:
                futs = {
                    pool.submit(
                        _run_one_question,
                        q=q,
                        spec=spec,
                        sys_cfg=sys_cfg,
                        runner=runner,
                        engine=engine,
                        schema_text=schema_text,
                        semantic_md=semantic_md,
                    ): q
                    for q in pending
                }
                for fut in as_completed(futs):
                    q = futs[fut]
                    payload = fut.result()
                    store.save_json(store.question_path(sys_dir, q.id), payload)
    finally:
        for eng in engines.values():
            eng.close()


def _score_run(
    spec: ExperimentSpec,
    store: RunStore,
    questions: list[BenchmarkQuestion],
    *,
    system_filter: str | None = None,
    overwrite: bool = False,
) -> None:
    schema_text = spec.dataset.schema_path.read_text(encoding="utf-8")
    judge = LLMJudge.from_config(spec.judge)
    systems = [
        s.id for s in spec.systems if system_filter is None or s.id == system_filter
    ]

    async def _run_all() -> None:
        for sys_id in systems:
            passed, failed, excluded = await score_system(
                spec=spec,
                store=store,
                judge=judge,
                system_id=sys_id,
                questions=questions,
                schema_text=schema_text,
                concurrency=spec.judge.concurrency,
                overwrite=overwrite,
            )
            console.print(
                f"[green]Scored {sys_id}[/green]: pass={passed}, fail={failed}, excluded={excluded}"
            )

    asyncio.run(_run_all())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cube", description="cube Text-to-SQL benchmark CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("ref", "run", "score", "eval"):
        p = sub.add_parser(name)
        p.add_argument("experiment", type=Path, help="Path to experiment YAML")
        p.add_argument("--run-id", default=None, help="Run identifier under runs/")
        p.add_argument("--only", default=None, help="Comma-separated question IDs")
        p.add_argument("--limit", type=int, default=None, help="Limit number of questions")
        p.add_argument("--overwrite", action="store_true", help="Overwrite existing artifacts")
        if name in ("run", "score"):
            p.add_argument("--system", default=None, help="Run/score only this system id")
        if name in ("run", "eval"):
            p.add_argument(
                "--skip-lifecycle",
                action="store_true",
                help="Skip experiment lifecycle before_run/after_run hooks",
            )

    rep = sub.add_parser("report")
    rep.add_argument("run_id", help="Run identifier under runs/")
    rep.add_argument("--runs-root", type=Path, default=None)
    rep.add_argument("--dataset", type=Path, default=None)
    rep.add_argument(
        "--metric",
        default="analytical_verdict",
        choices=("analytical_verdict", "verdict"),
    )
    rep.add_argument("--include-paper", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "report":
        return print_report(
            args.run_id,
            runs_root=args.runs_root,
            dataset_path=args.dataset,
            metric=args.metric,
            include_paper=args.include_paper,
            console=console,
        )

    spec = load_experiment(args.experiment)
    run_id = args.run_id or _default_run_id(spec)
    store = RunStore(run_id)
    store.save_experiment_snapshot(args.experiment)

    questions = _filter_questions(
        load_benchmark(spec.dataset.path),
        spec,
        only=args.only,
        limit=args.limit,
    )

    if args.command == "ref":
        _run_reference(spec, store, questions, overwrite=args.overwrite)
        console.print(f"[green]Reference complete[/green] -> {store.reference_dir}")
        return 0

    if args.command == "run":
        _run_reference(spec, store, questions, overwrite=False)
        with _lifecycle_guard(spec, skip=args.skip_lifecycle):
            _run_systems(
                spec,
                store,
                questions,
                system_filter=args.system,
                overwrite=args.overwrite,
            )
        return 0

    if args.command == "score":
        _score_run(
            spec,
            store,
            questions,
            system_filter=args.system,
            overwrite=args.overwrite,
        )
        return 0

    if args.command == "eval":
        _run_reference(spec, store, questions, overwrite=args.overwrite)
        with _lifecycle_guard(spec, skip=args.skip_lifecycle):
            _run_systems(spec, store, questions, overwrite=args.overwrite)
        _score_run(spec, store, questions, overwrite=args.overwrite)
        return print_report(run_id, console=console)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
