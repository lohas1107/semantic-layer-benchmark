"""Run the Open WebUI agentic benchmark (PostgreSQL + self-built run_sql tool).

Usage:
  uv run --env-file .env python scripts/run_openwebui.py --run-id RUN \
      --model google/gemma-4-26B-A4B-it [--semantic] [--limit N] [--only 1,2,3]

Env: OPENWEBUI_URL, OPENWEBUI_API_KEY, OPENWEBUI_RUN_SQL_TOOL_ID (default run_sql),
     PG_DSN (harness re-executes the final SQL).

Writes runs/RUN/openwebui_<model>_<raw|semantic>/qNN.json in the same shape as
the Claude/Codex runners, plus the agent transcript (tool_calls, rounds).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress

from benchmarks.text_to_sql.benchmark_data import load_benchmark
from benchmarks.text_to_sql.config import REPO_ROOT
from benchmarks.text_to_sql.engines import get_engine
from benchmarks.text_to_sql.paths import ensure, question_path, run_dir
from benchmarks.text_to_sql.runners.openwebui import (
    OpenWebUIClient,
    build_prompts,
    run_openwebui_agent,
)


def model_slug(model: str, *, semantic: bool) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    return f"openwebui_{base}_{'semantic' if semantic else 'raw'}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", required=True, help="Open WebUI model id")
    parser.add_argument("--semantic", action="store_true",
                        help="Prepend semantic_layer.pg.md to the prompt")
    parser.add_argument("--limit", type=int, help="Only the first N questions")
    parser.add_argument("--only", help="Comma-separated question ids")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=300.0, help="Seconds per question")
    parser.add_argument("--keep-chats", action="store_true",
                        help="Keep chats in Open WebUI for inspection")
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env", override=False)
    console = Console()
    base_url = os.getenv("OPENWEBUI_URL", "http://localhost:3000")
    api_key = os.getenv("OPENWEBUI_API_KEY")
    tool_id = os.getenv("OPENWEBUI_RUN_SQL_TOOL_ID", "run_sql")
    if not api_key:
        console.print("[red]OPENWEBUI_API_KEY is not set.[/red]")
        return 1

    engine = get_engine("postgres")
    schema_text = engine.schema_fixture.read_text(encoding="utf-8")
    semantic_md = None
    if args.semantic:
        if not engine.semantic_fixture.exists():
            console.print(f"[red]{engine.semantic_fixture} missing.[/red]")
            return 1
        semantic_md = engine.semantic_fixture.read_text(encoding="utf-8")

    questions = load_benchmark(engine.benchmark_json)
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        questions = [q for q in questions if q.id in wanted]
    if args.limit:
        questions = questions[: args.limit]

    slug = model_slug(args.model, semantic=args.semantic)
    out_dir = ensure(run_dir(args.run_id) / slug)
    todo = [q for q in questions if not question_path(out_dir, q.id).exists()]
    console.print(f"[cyan]{slug} → {out_dir} ({len(todo)} to run)[/cyan]")

    owui = OpenWebUIClient(base_url, api_key, timeout_s=args.timeout)
    owui_version = owui.version()
    db = engine.make_client()

    def run_one(q) -> None:
        system_prompt, user_prompt = build_prompts(
            schema_text=schema_text, question_text=q.question, semantic_md=semantic_md
        )
        res = run_openwebui_agent(
            owui, model=args.model, tool_ids=[tool_id], system_prompt=system_prompt,
            user_prompt=user_prompt, title=f"bench {args.run_id} {slug} q{q.id}",
            keep_chat=args.keep_chats,
        )
        if res.infra_error:
            # outage, not a model failure: leave no file so a rerun resumes this question
            raise RuntimeError(f"infra error after {res.attempts} attempts: {res.error}")
        rec: dict = {
            "question_id": q.id,
            "model": args.model,
            "variant": "semantic" if args.semantic else "raw",
            "sql": res.sql,
            "rows": None,
            "columns": None,
            "row_count": None,
            "latency_ms_gen": res.latency_ms_gen,
            "latency_ms_exec": None,
            "tokens_in": res.tokens_in,
            "tokens_out": res.tokens_out,
            "claude_error": res.error,  # key kept for scorer compat
            "sql_error": None,
            "rounds": res.rounds,
            "tool_calls": res.tool_calls,
            "final_content": res.final_content,
            "system_prompt": system_prompt,
            "openwebui_version": owui_version,
            "tool_id": tool_id,
            "attempts": res.attempts,
        }
        if res.sql:
            try:
                ex = db.execute(res.sql, timeout_s=30, row_cap=100)
                rec.update(rows=ex.rows, columns=ex.columns, row_count=len(ex.rows),
                           latency_ms_exec=ex.exec_ms)
            except engine.error as exc:
                rec["sql_error"] = str(exc)
        question_path(out_dir, q.id).write_text(
            json.dumps(rec, default=str, indent=2), encoding="utf-8"
        )

    failures = 0
    try:
        with Progress() as progress, ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            task = progress.add_task(slug, total=len(todo))
            futures = {pool.submit(run_one, q): q for q in todo}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:  # infra error or crash: no file, so a rerun resumes it
                    failures += 1
                    console.print(f"[red]q{futures[fut].id}: {exc}[/red]")
                progress.advance(task)
    finally:
        owui.close()

    console.print(f"[green]Open WebUI pass done: {out_dir} ({failures} crashed)[/green]")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
