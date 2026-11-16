"""Generate new benchmark questions + reference SQL per tier, validate on CH.

Asks Claude Opus to produce N candidate (question, sql) pairs for a given
tier, then executes each SQL against ClickHouse and keeps those that run
successfully and return 1+ rows. Writes survivors to
candidates/<tier>-<timestamp>.json for later curation.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cube.config import REPO_ROOT
from cube.engines.clickhouse import ClickHouseError, make_clickhouse_engine

MODEL = "claude-opus-4-7"
SCHEMA_FIXTURE = REPO_ROOT / "fixtures" / "retail_schema.sql"
SEMANTIC_FIXTURE = REPO_ROOT / "fixtures" / "semantic_layer.md"
BENCHMARK_JSON = REPO_ROOT / "text_to_sql_benchmark.json"
OUT_DIR = REPO_ROOT / "candidates"

TIER_GUIDE = {
    "simple": "Single-table queries. Basic COUNT/SUM/AVG/MIN/MAX. Simple WHERE filter. "
              "2-10 expected rows typically. Analyst basic.",
    "medium": "2-3 table joins. GROUP BY on 1 dimension. HAVING for filtering. "
              "Date functions (toYear, toMonth). Multiple aggregations per row.",
    "complex": "Window functions (row_number, rank, running totals). MAX(date) per group "
               "pattern for snapshots. Ratio calculations with NULL-safe division. "
               "3+ table joins. Subqueries. 10-50 expected rows.",
    "advanced": "Multiple CTEs (3+). Pivot logic via CASE WHEN ScenarioKey. Cohort filters. "
                "Nested aggregations. Self-joins (employee/entity hierarchy). "
                "Business metrics (LTV, churn, MAPE, gross margin).",
    "expert": "Ambiguous business terms requiring interpretation ('most profitable', 'performing well'). "
              "Implicit data context (infer 'last quarter' = Q4 2009 from data). "
              "Recursive CTE (entity/account hierarchy rollup). Correlation questions. "
              "Business-judgment metric definitions.",
}

SANDBOX = Path("/tmp/benchmark-sandbox")

SYSTEM_PROMPT = (
    "You are a senior data analyst generating benchmark questions for a Contoso "
    "retail ClickHouse dataset. Your job: produce exactly N distinct "
    "(question, reference_sql) pairs at a specified difficulty tier, each "
    "targeting a DIFFERENT business concept so the set has good coverage.\n\n"
    "Constraints:\n"
    "- SQL must execute on ClickHouse with the provided schema (database: retail).\n"
    "- Reference SQL must return a sensible, small-to-moderate result set "
    "(1-100 rows; avoid unbounded dumps).\n"
    "- Use ClickHouse-native functions (toYear, toMonth, toQuarter, argMax, "
    "countDistinct, sumIf, quantile, corr, etc.).\n"
    "- Honor data quirks from the semantic layer: online default = FactOnlineSales, "
    "all-channels = FactSales, FactInventory is snapshot (use MAX(DateKey) per "
    "(product, store)), string booleans (IsWorkDay = 'WorkDay'/'WeekEnd', "
    "IsHoliday = 'Yes'/'No'), PromotionKey=1 = 'No Discount', data range 2007-2009.\n"
    "- Question text must be natural business-analyst language — the kind of "
    "question a BI user types into a chat interface. No SQL jargon.\n"
    "- AVOID questions that duplicate existing benchmark Q1-Q50 patterns "
    "(listed below).\n"
    "- Output STRICT JSON array only, no markdown fences, no commentary.\n\n"
    'Schema: {"question": string, "sql": string, "tricky_aspects": [string], "tier_justification": string}'
)

JSON_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "sql": {"type": "string"},
                        "tricky_aspects": {"type": "array", "items": {"type": "string"}},
                        "tier_justification": {"type": "string"},
                    },
                    "required": ["question", "sql", "tricky_aspects", "tier_justification"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
    separators=(",", ":"),
)


def build_prompt(
    *, tier: str, n: int, schema: str, semantic: str, existing_questions: list[str]
) -> str:
    tier_desc = TIER_GUIDE[tier]
    existing_list = "\n".join(f"- Q{i+1}: {q}" for i, q in enumerate(existing_questions))
    return (
        f"Tier: {tier}\n"
        f"Tier description: {tier_desc}\n"
        f"Generate: {n} distinct questions + reference SQL.\n\n"
        f"=== EXISTING QUESTIONS (AVOID DUPLICATING THESE PATTERNS) ===\n{existing_list}\n\n"
        f"=== SEMANTIC LAYER ===\n{semantic}\n\n"
        f"=== RAW SCHEMA DDL ===\n{schema}\n\n"
        f"Return the JSON array of {n} entries. Each entry's SQL must be an executable ClickHouse query."
    )


def _parse_claude_output(stdout: bytes) -> list[dict]:
    obj = json.loads(stdout.decode("utf-8", errors="replace"))
    inner = obj.get("structured_output")
    if inner is None:
        raw = obj.get("result", "") or ""
        raw = re.sub(r"^```[a-zA-Z]*\s*\n?|\n?```\s*$", "", raw.strip(), flags=re.MULTILINE)
        inner = json.loads(raw)
    # Unwrap {"questions": [...]} envelope.
    if isinstance(inner, dict) and "questions" in inner:
        inner = inner["questions"]
    if not isinstance(inner, list):
        raise ValueError("expected JSON array (or {questions: [...]})")
    return inner


def generate(tier: str, n: int, schema: str, semantic: str, existing: list[str]) -> list[dict]:
    SANDBOX.mkdir(parents=True, exist_ok=True)
    sbx = SANDBOX / uuid.uuid4().hex
    sbx.mkdir()
    user_prompt = build_prompt(
        tier=tier, n=n, schema=schema, semantic=semantic, existing_questions=existing
    )
    cmd = [
        "claude",
        "-p",
        "--model", MODEL,
        "--tools", "",
        "--system-prompt", SYSTEM_PROMPT,
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format", "json",
        "--json-schema", JSON_SCHEMA,
        "--max-budget-usd", "5",
    ]
    print(f"  [claude] generating {n} candidates for {tier}...", flush=True)
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=sbx,
        input=user_prompt.encode("utf-8"),
        capture_output=True,
        timeout=600,
        check=False,
    )
    latency = int((time.perf_counter() - start) * 1000)
    if proc.returncode != 0:
        print(f"  [claude] exit={proc.returncode} stderr={proc.stderr[:300]!r}")
        return []
    print(f"  [claude] ok in {latency}ms")
    try:
        return _parse_claude_output(proc.stdout)
    except Exception as exc:
        print(f"  [claude] parse error: {exc}")
        return []


def validate(candidates: list[dict], client) -> list[dict]:
    kept: list[dict] = []
    for i, c in enumerate(candidates):
        sql = (c.get("sql") or "").strip()
        if not sql:
            print(f"  [validate] #{i+1} empty sql")
            continue
        try:
            res = client.execute(sql, timeout_s=30, row_cap=100)
        except ClickHouseError as exc:
            print(f"  [validate] #{i+1} FAIL: {str(exc)[:120]}")
            continue
        if not res.rows:
            print(f"  [validate] #{i+1} empty result, skipping")
            continue
        print(f"  [validate] #{i+1} ok ({len(res.rows)} rows)")
        kept.append({
            **c,
            "reference_rows": res.rows[:5],
            "reference_row_count": len(res.rows),
            "reference_columns": res.columns,
        })
    return kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", required=True, choices=list(TIER_GUIDE))
    parser.add_argument("--n", type=int, default=15)
    args = parser.parse_args(argv)

    schema = SCHEMA_FIXTURE.read_text(encoding="utf-8")
    semantic = SEMANTIC_FIXTURE.read_text(encoding="utf-8")
    benchmark = json.loads(BENCHMARK_JSON.read_text(encoding="utf-8"))
    existing = [q["question"] for q in benchmark["questions"]]

    print(f"Generating {args.n} {args.tier} candidates...")
    cands = generate(args.tier, args.n, schema, semantic, existing)
    print(f"Got {len(cands)} candidates from Claude.\n")

    client = make_clickhouse_engine()
    print("Validating against ClickHouse...")
    kept = validate(cands, client)
    print(f"\n{len(kept)}/{len(cands)} candidates passed validation.\n")

    OUT_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%H%M%S")
    out_path = OUT_DIR / f"{args.tier}-{ts}.json"
    out_path.write_text(
        json.dumps({"tier": args.tier, "candidates": kept}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
