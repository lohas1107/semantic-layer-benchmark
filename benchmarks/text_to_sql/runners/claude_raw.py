"""Claude-raw runner: subprocess wrapper around `claude -p`."""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

CLAUDE_RAW_MODEL = "claude-opus-4-7"

CLAUDE_RAW_SYSTEM_PROMPT = (
    "You are a ClickHouse SQL expert. Given the schema below, write a single "
    "SQL SELECT query that answers the user's question. Output only SQL — "
    "no markdown fences, no commentary, no explanation. Prefer ClickHouse-native "
    "functions (toYear, toMonth, toQuarter, sum, count, etc.)."
)

SANDBOX_ROOT = Path("/tmp/benchmark-sandbox")

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n?|\n?```\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ClaudeRawParseResult:
    sql: str
    tokens_in: int
    tokens_out: int
    is_error: bool


@dataclass(frozen=True)
class ClaudeRawRunResult:
    sql: str | None
    latency_ms_gen: int
    tokens_in: int
    tokens_out: int
    claude_error: str | None
    raw_stdout: str


def strip_sql_fences(s: str) -> str:
    """Remove ```sql / ``` fences and surrounding whitespace."""
    s = s.strip()
    if s.startswith("```"):
        s = _FENCE_RE.sub("", s).strip()
    return s


def build_user_prompt(schema_text: str, question_text: str) -> str:
    return f"{schema_text}\n\nQuestion: {question_text}"


def parse_claude_cli_output(stdout: bytes) -> ClaudeRawParseResult:
    obj = json.loads(stdout.decode("utf-8", errors="replace"))
    raw_result = obj.get("result", "") or ""
    usage = obj.get("usage") or {}
    # Claude CLI splits input tokens across 3 fields:
    #   input_tokens: uncached delta only
    #   cache_creation_input_tokens: tokens being newly cached this call
    #   cache_read_input_tokens: tokens served from cache
    # The comparable "total input tokens" is the sum.
    tokens_in_total = (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
    )
    return ClaudeRawParseResult(
        sql=strip_sql_fences(raw_result),
        tokens_in=tokens_in_total,
        tokens_out=int(usage.get("output_tokens", 0) or 0),
        is_error=bool(obj.get("is_error", False)),
    )


def _make_sandbox() -> Path:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    sbx = SANDBOX_ROOT / uuid.uuid4().hex
    sbx.mkdir()
    return sbx


def run_claude_raw(
    *,
    schema_text: str,
    question_text: str,
    timeout_s: int = 120,
    max_budget_usd: float = 2.0,
    model: str = CLAUDE_RAW_MODEL,
    effort: str = "medium",
) -> ClaudeRawRunResult:
    """Invoke `claude -p` with pinned flags, return SQL + usage + errors.

    `effort` selects the reasoning budget: low / medium / high / max.
    Supported on Opus 4.7, Opus 4.6, Sonnet 4.6.
    """
    user_prompt = build_user_prompt(schema_text, question_text)
    # Pass the prompt via stdin to avoid the CLI misinterpreting leading '--'
    # characters (SQL comments) in the schema as option flags.
    cmd = [
        "claude",
        "-p",
        "--effort", effort,
        "--model", model,
        "--tools", "",
        "--system-prompt", CLAUDE_RAW_SYSTEM_PROMPT,
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format", "json",
        "--max-budget-usd", str(max_budget_usd),
    ]
    sbx = _make_sandbox()
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=sbx,
            input=user_prompt.encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        latency = int((time.perf_counter() - start) * 1000)
        return ClaudeRawRunResult(
            sql=None,
            latency_ms_gen=latency,
            tokens_in=0,
            tokens_out=0,
            claude_error=f"timeout after {timeout_s}s: {exc}",
            raw_stdout="",
        )
    latency = int((time.perf_counter() - start) * 1000)

    if proc.returncode != 0:
        return ClaudeRawRunResult(
            sql=None,
            latency_ms_gen=latency,
            tokens_in=0,
            tokens_out=0,
            claude_error=(
                f"claude exit {proc.returncode}; stderr: "
                f"{proc.stderr.decode('utf-8', errors='replace')[:500]}"
            ),
            raw_stdout=proc.stdout.decode("utf-8", errors="replace"),
        )

    try:
        parsed = parse_claude_cli_output(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        return ClaudeRawRunResult(
            sql=None,
            latency_ms_gen=latency,
            tokens_in=0,
            tokens_out=0,
            claude_error=f"invalid claude output: {exc}",
            raw_stdout=proc.stdout.decode("utf-8", errors="replace"),
        )

    if parsed.is_error:
        return ClaudeRawRunResult(
            sql=None,
            latency_ms_gen=latency,
            tokens_in=parsed.tokens_in,
            tokens_out=parsed.tokens_out,
            claude_error="claude returned is_error=true",
            raw_stdout=proc.stdout.decode("utf-8", errors="replace"),
        )

    return ClaudeRawRunResult(
        sql=parsed.sql or None,
        latency_ms_gen=latency,
        tokens_in=parsed.tokens_in,
        tokens_out=parsed.tokens_out,
        claude_error=None,
        raw_stdout=proc.stdout.decode("utf-8", errors="replace"),
    )
