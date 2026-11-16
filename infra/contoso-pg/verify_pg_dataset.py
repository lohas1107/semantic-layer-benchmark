"""Verify the PostgreSQL port against the paper's ClickHouse reference answers.

1. Schema: every retail.* table/column of fixtures/retail_schema.pg.sql exists
   in the database with the expected type and nullability.
2. Data: run each PostgreSQL reference query and compare with
   published-runs/paper-2026-04-22/reference/qNN.json (ClickHouse results).
   Numbers match within a relative 1e-6 (or 0.01 absolute when both values
   carry <= 2 decimals, i.e. rounded output); text matches exactly.
   Row order matters only when the outer query has ORDER BY. When the
   reference was capped at 100 rows and is unordered, its rows must be a
   subset of the full PostgreSQL result.
   "@ties" questions compare only the tied ORDER BY key columns; "@deviation"
   questions are reported as known_deviation instead of failing.

Usage: uv run python docker/contoso/verify_pg_dataset.py [--only 1,2] [--json out.json]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json
import math
import re

from cube.config import REPO_ROOT
from cube.engines.postgres import PostgresError, make_postgres_engine
from cube.types import load_benchmark

PAPER_REF = REPO_ROOT / "archive" / "paper-2026-04-22" / "published-runs" / "reference"
PG_BENCH = REPO_ROOT / "datasets" / "postgres" / "benchmark.json"
PG_DDL = REPO_ROOT / "datasets" / "postgres" / "schema.sql"
REF_CAP = 100


def outer_has_order_by(sql: str) -> bool:
    s = re.sub(r"--[^\n]*", "", sql)
    s = re.sub(r"'[^']*'", "''", s)
    prev = None
    while prev != s:  # drop parenthesised groups innermost-first
        prev, s = s, re.sub(r"\([^()]*\)", "", s)
    return re.search(r"\border\s+by\b", s, re.I) is not None


def _decimals(x: float) -> int:
    t = repr(float(x))
    return len(t.split(".")[1]) if "." in t and "e" not in t else 99


def val_eq(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        fa, fb = float(a), float(b)
        if math.isinf(fa) or math.isinf(fb) or math.isnan(fa) or math.isnan(fb):
            return str(fa) == str(fb)
        if math.isclose(fa, fb, rel_tol=1e-6, abs_tol=1e-6):
            return True
        return _decimals(fa) <= 2 and _decimals(fb) <= 2 and abs(fa - fb) <= 0.0100001
    return str(a) == str(b)


def row_eq(r1, r2) -> bool:
    return len(r1) == len(r2) and all(val_eq(x, y) for x, y in zip(r1, r2, strict=True))


def multiset_subset(small: list, big: list) -> tuple[bool, list]:
    """Greedy match every row of small to a distinct row of big."""
    used = [False] * len(big)
    missing = []
    for r in small:
        for i, c in enumerate(big):
            if not used[i] and row_eq(r, c):
                used[i] = True
                break
        else:
            missing.append(r)
    return not missing, missing


def compare(ref: dict, pg_rows: list, ordered: bool,
            ties: list[int] | None = None) -> tuple[str, str]:
    ref_rows = ref["rows"] or []
    if ties is not None and ref_rows:
        proj = lambda rows: [[r[i] for i in ties] for r in rows]  # noqa: E731
        st, detail = compare({**ref, "rows": proj(ref_rows)}, proj(pg_rows), ordered)
        return ("match_ties" if st.startswith("match") else st), f"tie keys {ties}: {detail}"
    if ref.get("error") or ref.get("rows") is None:
        return "ref_error", (ref.get("error") or "no reference rows")[:200]
    if ref_rows and pg_rows and len(ref_rows[0]) != len(pg_rows[0]):
        return "mismatch", f"column count {len(ref_rows[0])} vs {len(pg_rows[0])}"
    capped = len(ref_rows) >= REF_CAP
    if ordered:
        head = pg_rows[: len(ref_rows)]
        if not capped and len(pg_rows) != len(ref_rows):
            return "mismatch", f"row count {len(ref_rows)} vs {len(pg_rows)}"
        bad = [i for i, (a, b) in enumerate(zip(ref_rows, head, strict=False)) if not row_eq(a, b)]
        if not bad:
            return "match", "ordered"
        ok, missing = multiset_subset(ref_rows, pg_rows if capped else head)
        if ok:
            return "match_unordered_ties", f"order differs at rows {bad[:5]} (ties)"
        return "mismatch", f"first diff row {bad[0]}: ref={ref_rows[bad[0]]} pg={head[bad[0]]}"
    if not capped and len(pg_rows) != len(ref_rows):
        ok, missing = multiset_subset(ref_rows, pg_rows)
        return "mismatch", (f"row count {len(ref_rows)} vs {len(pg_rows)}; "
                            f"{len(missing)} ref rows missing, e.g. {missing[:1]}")
    ok, missing = multiset_subset(ref_rows, pg_rows)
    if ok:
        return "match", "subset of full result" if capped else "multiset"
    return "mismatch", f"{len(missing)} ref rows not found, e.g. {missing[:2]}"


def check_schema(client) -> list[str]:
    expected: dict[tuple[str, str], tuple[str, bool]] = {}
    ddl = PG_DDL.read_text()
    for table, body in re.findall(r"CREATE TABLE retail\.(\w+)\s*\((.*?)\);", ddl, re.S):
        for line in body.strip().splitlines():
            name, rest = line.strip().rstrip(",").split(" ", 1)
            expected[(table.lower(), name.lower())] = (rest.replace(" NOT NULL", ""),
                                                       "NOT NULL" not in rest)
    res = client.execute(
        "SELECT table_name, column_name, data_type, is_nullable = 'YES' "
        "FROM information_schema.columns WHERE table_schema = 'retail'", row_cap=10_000)
    actual = {(t, c): (dt, n) for t, c, dt, n in res.rows}
    problems = [f"missing {k}" for k in expected if k not in actual]
    problems += [f"unexpected {k}" for k in actual if k not in expected]
    problems += [f"{k}: expected {expected[k]}, got {actual[k]}"
                 for k in expected if k in actual and expected[k] != actual[k]]
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args(argv)

    client = make_postgres_engine()
    schema_problems = check_schema(client)
    print(f"schema: {'OK' if not schema_problems else f'{len(schema_problems)} problems'}")
    for p in schema_problems[:30]:
        print("  ", p)

    questions = load_benchmark(PG_BENCH)
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        questions = [q for q in questions if q.id in wanted]
    meta = {q["id"]: q for q in json.loads(PG_BENCH.read_text())["questions"]}
    results = []
    for q in questions:
        ref = json.loads((PAPER_REF / f"q{q.id:02d}.json").read_text())
        m = meta[q.id]
        try:
            res = client.execute(q.expected_sql, timeout_s=args.timeout, row_cap=2_000_000)
            status, detail = compare(ref, res.rows, outer_has_order_by(q.expected_sql),
                                     m.get("verify_ties"))
            ms = res.exec_ms
        except PostgresError as exc:
            status, detail, ms = "pg_error", str(exc)[:300], None
        if m.get("verify_deviation") and not status.startswith("match"):
            status, detail = "known_deviation", f"{m['verify_deviation']} | {detail}"
        results.append({"id": q.id, "status": status, "detail": detail, "exec_ms": ms})
        print(f"q{q.id:<3} {status:<22} {ms or '':>7} {detail[:160]}")

    summary: dict[str, int] = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    print("summary:", summary)
    if args.json:
        args.json.write_text(json.dumps({"schema": schema_problems, "results": results}, indent=2))
    ok_states = ("match", "match_unordered_ties", "match_ties", "known_deviation")
    bad = sum(v for k, v in summary.items() if k not in ok_states)
    return 1 if (bad or schema_problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
