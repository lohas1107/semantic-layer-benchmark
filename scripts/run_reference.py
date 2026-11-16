"""Execute every question's expected_sql against the chosen engine and save reference rows."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import json

from rich.console import Console
from rich.progress import Progress

from benchmarks.text_to_sql.benchmark_data import load_benchmark
from benchmarks.text_to_sql.engines import ENGINES, get_engine
from benchmarks.text_to_sql.paths import ensure, question_path, reference_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--engine", choices=sorted(ENGINES), default="clickhouse")
    args = parser.parse_args(argv)
    engine = get_engine(args.engine)

    console = Console()
    questions = load_benchmark(engine.benchmark_json)
    out_dir = ensure(reference_dir(args.run_id))
    client = engine.make_client()

    with Progress() as progress:
        task = progress.add_task("reference", total=len(questions))
        for q in questions:
            out_path = question_path(out_dir, q.id)
            if out_path.exists():
                progress.advance(task)
                continue
            rec: dict = {
                "question_id": q.id,
                "sql": q.expected_sql,
                "rows": None,
                "columns": None,
                "row_count": None,
                "exec_ms": None,
                "error": None,
            }
            try:
                res = client.execute(q.expected_sql, timeout_s=30, row_cap=100)
                rec["rows"] = res.rows
                rec["columns"] = res.columns
                rec["row_count"] = len(res.rows)
                rec["exec_ms"] = res.exec_ms
            except engine.error as exc:
                rec["error"] = str(exc)
            out_path.write_text(json.dumps(rec, default=str, indent=2), encoding="utf-8")
            progress.advance(task)

    console.print(f"[green]Reference pass done: {out_dir}[/green]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
