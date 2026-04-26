"""Unified Claude runner — parameterized by --model and --semantic.

Usage:
  uv run python scripts/run_claude.py --run-id RUN --model claude-opus-4-7
  uv run python scripts/run_claude.py --run-id RUN --model claude-sonnet-4-6 --semantic

Writes per-question JSON under runs/RUN/claude_<slug>_<variant>/.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import json
import re

from rich.console import Console
from rich.progress import Progress

from benchmarks.text_to_sql.benchmark_data import load_benchmark
from benchmarks.text_to_sql.clickhouse_client import ClickHouseError, make_client
from benchmarks.text_to_sql.config import REPO_ROOT
from benchmarks.text_to_sql.paths import ensure, question_path, run_dir
from benchmarks.text_to_sql.runners.claude_raw import run_claude_raw
from benchmarks.text_to_sql.runners.claude_semantic import run_claude_semantic

BENCHMARK_JSON = REPO_ROOT / "text_to_sql_benchmark.json"
SCHEMA_FIXTURE = REPO_ROOT / "fixtures" / "retail_schema.sql"
SEMANTIC_FIXTURE = REPO_ROOT / "fixtures" / "semantic_layer.md"


def model_slug(model: str, *, semantic: bool) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    variant = "semantic" if semantic else "raw"
    return f"claude_{base}_{variant}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", required=True,
                        help="Claude model id (e.g., claude-opus-4-7, claude-sonnet-4-6)")
    parser.add_argument("--semantic", action="store_true")
    args = parser.parse_args(argv)

    console = Console()
    if not SCHEMA_FIXTURE.exists():
        console.print("[red]fixtures/retail_schema.sql missing.[/red]")
        return 1
    schema_text = SCHEMA_FIXTURE.read_text(encoding="utf-8")
    semantic_md = None
    if args.semantic:
        if not SEMANTIC_FIXTURE.exists():
            console.print("[red]fixtures/semantic_layer.md missing.[/red]")
            return 1
        semantic_md = SEMANTIC_FIXTURE.read_text(encoding="utf-8")

    slug = model_slug(args.model, semantic=args.semantic)
    out_dir = ensure(run_dir(args.run_id) / slug)
    console.print(f"[cyan]{slug} → {out_dir}[/cyan]")

    questions = load_benchmark(BENCHMARK_JSON)
    ch = make_client()

    with Progress() as progress:
        task = progress.add_task(slug, total=len(questions))
        for q in questions:
            out_path = question_path(out_dir, q.id)
            if out_path.exists():
                progress.advance(task)
                continue

            rec: dict = {
                "question_id": q.id,
                "model": args.model,
                "variant": "semantic" if args.semantic else "raw",
                "sql": None,
                "rows": None,
                "columns": None,
                "row_count": None,
                "latency_ms_gen": None,
                "latency_ms_exec": None,
                "tokens_in": 0,
                "tokens_out": 0,
                "claude_error": None,
                "sql_error": None,
            }

            if args.semantic:
                run_result = run_claude_semantic(
                    semantic_md=semantic_md,
                    schema_text=schema_text,
                    question_text=q.question,
                    model=args.model,
                )
            else:
                run_result = run_claude_raw(
                    schema_text=schema_text,
                    question_text=q.question,
                    model=args.model,
                )

            rec["sql"] = run_result.sql
            rec["latency_ms_gen"] = run_result.latency_ms_gen
            rec["tokens_in"] = run_result.tokens_in
            rec["tokens_out"] = run_result.tokens_out
            rec["claude_error"] = run_result.claude_error

            if run_result.sql:
                try:
                    res = ch.execute(run_result.sql, timeout_s=30, row_cap=100)
                    rec["rows"] = res.rows
                    rec["columns"] = res.columns
                    rec["row_count"] = len(res.rows)
                    rec["latency_ms_exec"] = res.exec_ms
                except ClickHouseError as exc:
                    rec["sql_error"] = str(exc)

            out_path.write_text(json.dumps(rec, default=str, indent=2), encoding="utf-8")
            progress.advance(task)

    console.print(f"[green]Claude pass done: {out_dir}[/green]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
