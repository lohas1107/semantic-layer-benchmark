"""Run Codex (OpenAI CLI) against every benchmark question.

Usage:
  uv run python scripts/run_codex.py --run-id RUN --model gpt-5.4 [--semantic]

Writes per-question JSON under runs/RUN/codex_<slug>/ where <slug> is derived
from the model + variant (e.g., codex_5_4_raw, codex_5_4_semantic).
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
from benchmarks.text_to_sql.runners.codex import run_codex

BENCHMARK_JSON = REPO_ROOT / "text_to_sql_benchmark.json"
SCHEMA_FIXTURE = REPO_ROOT / "fixtures" / "retail_schema.sql"
SEMANTIC_FIXTURE = REPO_ROOT / "fixtures" / "semantic_layer.md"


def model_slug(model: str, *, semantic: bool) -> str:
    """Turn model name into a filesystem-safe slug + variant suffix."""
    base = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    variant = "semantic" if semantic else "raw"
    return f"codex_{base}_{variant}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", required=True,
                        help="Codex model name (e.g., gpt-5.4, gpt-5.2-codex)")
    parser.add_argument("--semantic", action="store_true",
                        help="Prepend semantic_layer.md to the prompt")
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
                "claude_error": None,  # kept for scorer compat
                "sql_error": None,
            }
            run_result = run_codex(
                model=args.model,
                schema_text=schema_text,
                question_text=q.question,
                semantic_md=semantic_md,
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

    console.print(f"[green]Codex pass done: {out_dir}[/green]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
