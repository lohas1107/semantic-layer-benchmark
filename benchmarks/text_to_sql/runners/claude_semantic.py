"""Claude-semantic runner: claude -p with BOTH raw DDL AND semantic layer markdown.

Identical pipeline to claude_raw except the user prompt prepends a semantic
layer markdown describing cube purposes, calculated metrics, steering rules.
Everything else (model, flags, parsing, error handling) is shared.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from benchmarks.text_to_sql.runners.claude_raw import (
    CLAUDE_RAW_MODEL,
    ClaudeRawRunResult,
    _make_sandbox,
    parse_claude_cli_output,
)

CLAUDE_SEMANTIC_SYSTEM_PROMPT = (
    "You are a ClickHouse SQL expert. Given (1) a SEMANTIC LAYER markdown that "
    "explains the business meaning of tables, columns, and calculated metrics, "
    "and (2) the raw DDL for the schema, write a single SQL SELECT query that "
    "answers the user's question. Output only SQL — no markdown fences, no "
    "commentary, no explanation. Prefer ClickHouse-native functions (toYear, "
    "toMonth, toQuarter, sum, count). Follow the semantic-layer guidance: it "
    "supplies business context that is NOT derivable from the DDL alone."
)


def build_semantic_user_prompt(
    *, semantic_md: str, schema_text: str, question_text: str
) -> str:
    return (
        "# SEMANTIC LAYER\n\n"
        f"{semantic_md}\n\n"
        "# RAW DDL (authoritative for types)\n\n"
        f"{schema_text}\n\n"
        f"# Question: {question_text}"
    )


def run_claude_semantic(
    *,
    semantic_md: str,
    schema_text: str,
    question_text: str,
    timeout_s: int = 120,
    max_budget_usd: float = 2.0,
    model: str = CLAUDE_RAW_MODEL,
    effort: str = "medium",
) -> ClaudeRawRunResult:
    """Invoke `claude -p` with pinned flags + semantic layer + DDL.

    `effort`: low/medium/high/max. Supported on Opus 4.7, Opus 4.6, Sonnet 4.6.
    """
    user_prompt = build_semantic_user_prompt(
        semantic_md=semantic_md,
        schema_text=schema_text,
        question_text=question_text,
    )
    cmd = [
        "claude",
        "-p",
        "--effort", effort,
        "--model", model,
        "--tools", "",
        "--system-prompt", CLAUDE_SEMANTIC_SYSTEM_PROMPT,
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
    except Exception as exc:
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
