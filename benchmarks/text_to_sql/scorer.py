"""LLM-as-judge scorer. One judge call per (question, system). Sonnet 4.6."""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from benchmarks.text_to_sql.runners.claude_raw import SANDBOX_ROOT

JUDGE_MODEL = "claude-sonnet-4-6"

JUDGE_SYSTEM_PROMPT = (
    "You are grading a text-to-SQL system. Given a natural-language question, "
    "a database schema, a reference SQL query that produced the expected rows, "
    "and a candidate SQL query that produced candidate rows — return TWO "
    "verdicts on the candidate.\n\n"
    "STRICT verdict (row-level match):\n"
    "- pass if the candidate's row values match the expected rows, ignoring "
    "extra columns, column ordering, row ordering (unless the question "
    "requires ordering, e.g., 'top N', 'ranked', 'cumulative'), and minor "
    "numeric precision (less than 1% relative drift).\n"
    "- pass if the candidate uses different but semantically equivalent SQL "
    "(alternative joins, alternative aggregations) that yields the same rows.\n"
    "- Otherwise fail.\n\n"
    "ANALYTICAL verdict (OLAP-aware, more permissive):\n"
    "- pass in ALL cases where STRICT passes.\n"
    "- ALSO pass when the candidate returns a superset / breakdown of the "
    "expected answer that an analyst would accept as correct. Specifically:\n"
    "  * If the reference is a single scalar (e.g., total count, total sum) "
    "and the candidate returns a grouped breakdown (e.g., by year, by month, "
    "by category) whose numeric values AGGREGATE (sum / sum-distinct where "
    "appropriate) to the reference scalar within 1% relative drift — pass. "
    "Example: reference = 18869 unique customers; candidate = per-year rows "
    "summing to ≈18869 → analytical pass.\n"
    "  * If the reference has rows and the candidate has the SAME rows PLUS "
    "an extra 'null category' or 'no promotion' row that represents "
    "uncategorized items, and the other rows match — analytical pass "
    "(inclusion of 'N/A' bucket is valid OLAP behavior).\n"
    "  * If the reference returns a percentage and the candidate returns the "
    "same ratio as a decimal (0.0122 vs 1.22%), or vice versa — analytical "
    "pass.\n"
    "  * If the reference returns absolute dollar amounts and the candidate "
    "returns the equivalent percentage/ratio of the same measure — "
    "analytical pass (both answer the question, just in different units).\n"
    "- fail ONLY if the candidate answers a genuinely different question "
    "(wrong filter, wrong entity, wrong measure), has wildly wrong values "
    "that don't reduce to the reference via aggregation, or errored out.\n\n"
    'Return JSON only: {"strict_verdict": "pass"|"fail", '
    '"analytical_verdict": "pass"|"fail", "reason": "<one short sentence '
    'explaining the analytical verdict>"}.'
)

JUDGE_JSON_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "strict_verdict": {"enum": ["pass", "fail"]},
            "analytical_verdict": {"enum": ["pass", "fail"]},
            "reason": {"type": "string", "maxLength": 300},
        },
        "required": ["strict_verdict", "analytical_verdict", "reason"],
        "additionalProperties": False,
    },
    separators=(",", ":"),
)

PROMPT_HARD_CAP = 100_000
SCHEMA_SOFT_CAP = 32_000
SCHEMA_HARD_CAP = 4_000


@dataclass(frozen=True)
class JudgeOutcome:
    verdict: str  # strict: "pass" | "fail"
    analytical_verdict: str  # OLAP-aware: "pass" | "fail"
    reason: str
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class ScoreResult:
    verdict: str  # strict: "pass" | "fail" | "excluded"
    analytical_verdict: str  # OLAP-aware: "pass" | "fail" | "excluded"
    reason: str
    skipped_judge: bool
    judge_tokens_in: int
    judge_tokens_out: int
    judge_latency_ms: int


def build_judge_user_prompt(
    *,
    question: str,
    schema_text: str,
    expected_sql: str,
    expected_rows: list,
    expected_row_count: int,
    candidate_sql: str,
    candidate_rows: list,
    candidate_row_count: int,
    schema_char_limit: int = SCHEMA_SOFT_CAP,
    row_preview_limit: int = 10,
) -> str:
    schema = schema_text
    if len(schema) > schema_char_limit:
        schema = schema[:schema_char_limit] + "\n-- [TRUNCATED]\n"
    expected_preview = expected_rows[:row_preview_limit]
    candidate_preview = candidate_rows[:row_preview_limit]
    return (
        f"Question: {question}\n\n"
        f"Schema:\n{schema}\n\n"
        f"Reference SQL:\n{expected_sql}\n\n"
        f"Expected rows (first {len(expected_preview)} of {expected_row_count}):\n"
        f"{json.dumps(expected_preview, default=str)}\n\n"
        f"Candidate SQL:\n{candidate_sql}\n\n"
        f"Candidate rows (first {len(candidate_preview)} of {candidate_row_count}):\n"
        f"{json.dumps(candidate_preview, default=str)}\n"
    )


def parse_judge_output(stdout: bytes) -> JudgeOutcome:
    try:
        obj = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed judge output: {exc}") from exc
    # With --json-schema the CLI puts the parsed object at `structured_output`.
    # Without it (legacy/fixture shape), the JSON lives in `result` as a string.
    inner = obj.get("structured_output")
    if inner is None:
        result_str = obj.get("result") or ""
        try:
            inner = json.loads(result_str)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed result field: {exc}") from exc
    strict = inner.get("strict_verdict") or inner.get("verdict")
    analytical = inner.get("analytical_verdict") or strict
    reason = (inner.get("reason") or "").strip()
    if strict not in ("pass", "fail"):
        raise ValueError(f"invalid strict_verdict: {strict!r}")
    if analytical not in ("pass", "fail"):
        raise ValueError(f"invalid analytical_verdict: {analytical!r}")
    usage = obj.get("usage") or {}
    return JudgeOutcome(
        verdict=strict,
        analytical_verdict=analytical,
        reason=reason,
        tokens_in=int(usage.get("input_tokens", 0) or 0),
        tokens_out=int(usage.get("output_tokens", 0) or 0),
    )


def _make_sandbox() -> Path:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    sbx = SANDBOX_ROOT / uuid.uuid4().hex
    sbx.mkdir()
    return sbx


def _invoke_judge(user_prompt: str, *, timeout_s: int = 180) -> tuple[bytes, int]:
    """Shell out to `claude -p` for the judge model. Returns (stdout, latency_ms).

    Passes the prompt via stdin to avoid CLI misinterpretation of leading '--'.
    """
    cmd = [
        "claude",
        "-p",
        "--model", JUDGE_MODEL,
        "--tools", "",
        "--system-prompt", JUDGE_SYSTEM_PROMPT,
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format", "json",
        "--json-schema", JUDGE_JSON_SCHEMA,
    ]
    sbx = _make_sandbox()
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=sbx,
        input=user_prompt.encode("utf-8"),
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    latency = int((time.perf_counter() - start) * 1000)
    if proc.returncode != 0:
        raise RuntimeError(
            f"judge exit {proc.returncode}; stderr: "
            f"{proc.stderr.decode('utf-8', errors='replace')[:300]}"
        )
    return proc.stdout, latency


def _call_judge_with_retry(user_prompt: str) -> JudgeOutcome | None:
    """Invoke judge; retry once on subprocess failure or parse error. None on both fail."""
    for attempt in (1, 2):
        try:
            stdout, _latency = _invoke_judge(user_prompt)
            return parse_judge_output(stdout)
        except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
            if attempt == 2:
                return None
            continue
    return None


def score_one(
    *,
    question: str,
    schema_text: str,
    reference: dict,
    candidate: dict,
    candidate_sql_key: str = "sql",
) -> ScoreResult:
    """Score one (question, system) pair.

    Skip rules (in order):
      - Reference errored → verdict="excluded" (can't grade), skip judge.
      - Candidate has any error → verdict="fail", skip judge.
      - Both ref and candidate rows empty (and both executed ok) → trivial pass, skip.

    Otherwise: build prompt, invoke judge, return verdict.
    """
    # Reference first: an ungradable question is excluded for every system,
    # whether or not the candidate also errored (paper: q33 excluded, n=99).
    if reference.get("error"):
        return ScoreResult(
            verdict="excluded",
            analytical_verdict="excluded",
            reason=f"reference_error: {reference['error']}",
            skipped_judge=True,
            judge_tokens_in=0,
            judge_tokens_out=0,
            judge_latency_ms=0,
        )

    cand_error = (
        candidate.get("error")
        or candidate.get("claude_error")
        or candidate.get("sql_error")
    )
    if cand_error:
        return ScoreResult(
            verdict="fail",
            analytical_verdict="fail",
            reason=str(cand_error),
            skipped_judge=True,
            judge_tokens_in=0,
            judge_tokens_out=0,
            judge_latency_ms=0,
        )

    ref_rows = reference.get("rows") or []
    cand_rows = candidate.get("rows") or []
    if not ref_rows and not cand_rows:
        return ScoreResult(
            verdict="pass",
            analytical_verdict="pass",
            reason="trivial_empty_match",
            skipped_judge=True,
            judge_tokens_in=0,
            judge_tokens_out=0,
            judge_latency_ms=0,
        )

    user_prompt = build_judge_user_prompt(
        question=question,
        schema_text=schema_text,
        expected_sql=reference.get("sql", ""),
        expected_rows=ref_rows,
        expected_row_count=reference.get("row_count") or len(ref_rows),
        candidate_sql=candidate.get(candidate_sql_key) or "",
        candidate_rows=cand_rows,
        candidate_row_count=candidate.get("row_count") or len(cand_rows),
    )

    if len(user_prompt) > PROMPT_HARD_CAP:
        user_prompt = build_judge_user_prompt(
            question=question,
            schema_text=schema_text[:SCHEMA_HARD_CAP] + "\n-- [TRUNCATED]\n",
            expected_sql=reference.get("sql", ""),
            expected_rows=ref_rows,
            expected_row_count=reference.get("row_count") or len(ref_rows),
            candidate_sql=candidate.get(candidate_sql_key) or "",
            candidate_rows=cand_rows,
            candidate_row_count=candidate.get("row_count") or len(cand_rows),
            row_preview_limit=5,
        )

    if len(user_prompt) > PROMPT_HARD_CAP:
        return ScoreResult(
            verdict="fail",
            analytical_verdict="fail",
            reason="prompt_too_large",
            skipped_judge=True,
            judge_tokens_in=0,
            judge_tokens_out=0,
            judge_latency_ms=0,
        )

    start = time.perf_counter()
    outcome = _call_judge_with_retry(user_prompt)
    latency = int((time.perf_counter() - start) * 1000)

    if outcome is None:
        return ScoreResult(
            verdict="fail",
            analytical_verdict="fail",
            reason="judge_error",
            skipped_judge=False,
            judge_tokens_in=0,
            judge_tokens_out=0,
            judge_latency_ms=latency,
        )

    return ScoreResult(
        verdict=outcome.verdict,
        analytical_verdict=outcome.analytical_verdict,
        reason=outcome.reason,
        skipped_judge=False,
        judge_tokens_in=outcome.tokens_in,
        judge_tokens_out=outcome.tokens_out,
        judge_latency_ms=latency,
    )
