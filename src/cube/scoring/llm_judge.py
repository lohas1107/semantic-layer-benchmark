"""LLM-as-judge scorer supporting both `claude_cli` and `openai_compatible` backends."""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass

import openai

from cube.config import JudgeConfig
from cube.types import ScoreResult, Verdict

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
    verdict: Verdict
    analytical_verdict: Verdict
    reason: str
    tokens_in: int
    tokens_out: int


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


def parse_judge_cli_output(stdout: bytes) -> JudgeOutcome:
    try:
        obj = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed judge output: {exc}") from exc
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


def _parse_judge_json_text(text: str, *, tokens_in: int, tokens_out: int) -> JudgeOutcome:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    inner = json.loads(cleaned)
    strict = inner.get("strict_verdict") or inner.get("verdict")
    analytical = inner.get("analytical_verdict") or strict
    reason = (inner.get("reason") or "").strip()
    if strict not in ("pass", "fail"):
        raise ValueError(f"invalid strict_verdict: {strict!r}")
    if analytical not in ("pass", "fail"):
        raise ValueError(f"invalid analytical_verdict: {analytical!r}")
    return JudgeOutcome(
        verdict=strict,
        analytical_verdict=analytical,
        reason=reason,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


class LLMJudge:
    """Configurable LLM-as-judge supporting `claude_cli` and `openai_compatible`."""

    def __init__(
        self,
        *,
        runner: str = "claude_cli",
        model: str = "claude-sonnet-4-6",
        base_url: str | None = None,
        api_key: str | None = None,
        row_preview_limit: int = 10,
        timeout_s: int = 180,
        openai_client: openai.OpenAI | None = None,
    ) -> None:
        self.name = f"{runner}:{model}"
        self.runner = runner
        self.model = model
        self.row_preview_limit = row_preview_limit
        self.timeout_s = timeout_s
        self._openai_client = openai_client
        if runner == "openai_compatible" and self._openai_client is None:
            self._openai_client = openai.OpenAI(
                base_url=base_url,
                api_key=api_key or "EMPTY",
            )

    @classmethod
    def from_config(cls, cfg: JudgeConfig) -> LLMJudge:
        return cls(
            runner=cfg.runner,
            model=cfg.model,
            base_url=cfg.provider.get_base_url(),
            api_key=cfg.provider.get_api_key(),
            row_preview_limit=cfg.row_preview_limit,
            timeout_s=int(cfg.params.get("timeout_s", 180)),
        )

    def _invoke_claude_cli(self, user_prompt: str) -> JudgeOutcome:
        cmd = [
            "claude",
            "-p",
            "--model",
            self.model,
            "--tools",
            "",
            "--system-prompt",
            JUDGE_SYSTEM_PROMPT,
            "--disable-slash-commands",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--json-schema",
            JUDGE_JSON_SCHEMA,
        ]
        with tempfile.TemporaryDirectory(prefix="cube-judge-") as sbx:
            proc = subprocess.run(
                cmd,
                cwd=sbx,
                input=user_prompt.encode("utf-8"),
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"judge exit {proc.returncode}; stderr: "
                f"{proc.stderr.decode('utf-8', errors='replace')[:300]}"
            )
        return parse_judge_cli_output(proc.stdout)

    def _invoke_openai_compatible(self, user_prompt: str) -> JudgeOutcome:
        assert self._openai_client is not None
        resp = self._openai_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            timeout=self.timeout_s,
        )
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = resp.usage
        tin = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        tout = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        return _parse_judge_json_text(content, tokens_in=tin, tokens_out=tout)

    def _call_with_retry(self, user_prompt: str) -> JudgeOutcome | None:
        invoke = (
            self._invoke_claude_cli
            if self.runner == "claude_cli"
            else self._invoke_openai_compatible
        )
        for attempt in (1, 2):
            try:
                return invoke(user_prompt)
            except Exception:
                if attempt == 2:
                    return None
        return None

    def score_sync(
        self,
        *,
        question: str,
        schema_text: str,
        expected_sql: str,
        expected_rows: list[list],
        expected_row_count: int,
        candidate_sql: str | None,
        candidate_rows: list[list],
        candidate_row_count: int,
        candidate_error: str | None,
        reference_error: str | None,
    ) -> ScoreResult:
        if reference_error:
            return ScoreResult(
                verdict="excluded",
                analytical_verdict="excluded",
                reason=f"reference_error: {reference_error}",
                skipped_judge=True,
            )

        if candidate_error:
            return ScoreResult(
                verdict="fail",
                analytical_verdict="fail",
                reason=str(candidate_error),
                skipped_judge=True,
            )

        if not expected_rows and not candidate_rows:
            return ScoreResult(
                verdict="pass",
                analytical_verdict="pass",
                reason="both reference and candidate returned 0 rows",
                skipped_judge=True,
            )

        prompt = build_judge_user_prompt(
            question=question,
            schema_text=schema_text,
            expected_sql=expected_sql,
            expected_rows=expected_rows,
            expected_row_count=expected_row_count,
            candidate_sql=candidate_sql or "",
            candidate_rows=candidate_rows,
            candidate_row_count=candidate_row_count,
            row_preview_limit=self.row_preview_limit,
        )
        if len(prompt) > PROMPT_HARD_CAP:
            prompt = build_judge_user_prompt(
                question=question,
                schema_text=schema_text,
                expected_sql=expected_sql,
                expected_rows=expected_rows,
                expected_row_count=expected_row_count,
                candidate_sql=candidate_sql or "",
                candidate_rows=candidate_rows,
                candidate_row_count=candidate_row_count,
                schema_char_limit=SCHEMA_HARD_CAP,
                row_preview_limit=min(5, self.row_preview_limit),
            )

        start = time.perf_counter()
        outcome = self._call_with_retry(prompt)
        latency_ms = int((time.perf_counter() - start) * 1000)

        if outcome is None:
            return ScoreResult(
                verdict="fail",
                analytical_verdict="fail",
                reason="judge_error: failed after retry",
                skipped_judge=False,
                judge_latency_ms=latency_ms,
            )

        return ScoreResult(
            verdict=outcome.verdict,
            analytical_verdict=outcome.analytical_verdict,
            reason=outcome.reason,
            skipped_judge=False,
            judge_tokens_in=outcome.tokens_in,
            judge_tokens_out=outcome.tokens_out,
            judge_latency_ms=latency_ms,
        )

    async def score(
        self,
        *,
        question: str,
        schema_text: str,
        expected_sql: str,
        expected_rows: list[list],
        expected_row_count: int,
        candidate_sql: str | None,
        candidate_rows: list[list],
        candidate_row_count: int,
        candidate_error: str | None,
        reference_error: str | None,
    ) -> ScoreResult:
        return await asyncio.to_thread(
            self.score_sync,
            question=question,
            schema_text=schema_text,
            expected_sql=expected_sql,
            expected_rows=expected_rows,
            expected_row_count=expected_row_count,
            candidate_sql=candidate_sql,
            candidate_rows=candidate_rows,
            candidate_row_count=candidate_row_count,
            candidate_error=candidate_error,
            reference_error=reference_error,
        )
