"""Codex runner: subprocess wrapper around `codex exec --json`.

Generic — parameterized by `model` (e.g., 'gpt-5.4', 'gpt-5.2-codex') and
optional semantic_md. When semantic_md is None, emits the same DDL-only
prompt as claude_raw. When semantic_md is provided, prepends it like
claude_semantic.

One-shot enforced by reading only the first agent_message and disabling
web_search. Tokens come from the `turn.completed.usage` event.

Returns ClaudeRawRunResult shape so downstream scoring code is unchanged.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from benchmarks.text_to_sql.runners.claude_raw import (
    ClaudeRawRunResult,
    SANDBOX_ROOT,
    strip_sql_fences,
)


def _parse_jsonl_events(
    stdout: bytes,
) -> tuple[str | None, int, int, str | None]:
    """Return (agent_message, input_tokens, output_tokens, fail_reason).

    fail_reason is one of: None, "rate_limit", "shell_snapshot", "turn_failed:<msg>"
    Pulled from turn.failed events or item.completed type=error events.
    """
    text: str | None = None
    tin = tout = 0
    fail_reason: str | None = None
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev_type = ev.get("type")
        if ev_type == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and text is None:
                text = item.get("text") or None
            elif item.get("type") == "error":
                msg = (item.get("message") or "")[:300]
                if "shell_snapshot" in msg or "snapshot" in msg.lower():
                    fail_reason = fail_reason or "shell_snapshot"
        elif ev_type == "turn.completed":
            usage = ev.get("usage") or {}
            tin = int(usage.get("input_tokens", 0) or 0)
            tout = int(usage.get("output_tokens", 0) or 0)
        elif ev_type == "turn.failed":
            err = ev.get("error") or {}
            msg = (err.get("message") or "")[:300]
            low = msg.lower()
            if "usage limit" in low or "rate limit" in low or "rate-limit" in low:
                fail_reason = "rate_limit"
            else:
                fail_reason = f"turn_failed:{msg[:120]}"
        elif ev_type == "error":
            msg = (ev.get("message") or "")[:300]
            low = msg.lower()
            if "usage limit" in low or "rate limit" in low:
                fail_reason = fail_reason or "rate_limit"
    return text, tin, tout, fail_reason

CODEX_RAW_SYSTEM = (
    "You are a ClickHouse SQL expert. Given the schema below, write a single "
    "SQL SELECT query that answers the user's question. Output ONLY SQL — "
    "no markdown fences, no commentary, no explanation. Prefer ClickHouse-native "
    "functions (toYear, toMonth, toQuarter, sum, count, etc.)."
)

CODEX_SEMANTIC_SYSTEM = (
    "You are a ClickHouse SQL expert. Given (1) a SEMANTIC LAYER markdown "
    "explaining business meaning of tables and metrics, and (2) the raw "
    "DDL, write a single SQL SELECT query that answers the user's question. "
    "Output ONLY SQL — no markdown fences, no commentary. Prefer "
    "ClickHouse-native functions. Follow the semantic-layer guidance — it "
    "supplies business context not derivable from DDL alone."
)


def _build_prompt(*, schema: str, question: str, semantic_md: str | None) -> str:
    if semantic_md is None:
        sysblock = CODEX_RAW_SYSTEM
        body = f"{schema}\n\nQuestion: {question}"
    else:
        sysblock = CODEX_SEMANTIC_SYSTEM
        body = (
            "# SEMANTIC LAYER\n\n"
            f"{semantic_md}\n\n"
            "# RAW DDL (authoritative for types)\n\n"
            f"{schema}\n\n"
            f"# Question: {question}"
        )
    return f"{sysblock}\n\n{body}"


def run_codex(
    *,
    model: str,
    schema_text: str,
    question_text: str,
    semantic_md: str | None = None,
    timeout_s: int = 300,
    reasoning_effort: str = "medium",
) -> ClaudeRawRunResult:
    """Invoke `codex exec` with the given model + reasoning effort.

    reasoning_effort: 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' (codex range)
    """
    user_prompt = _build_prompt(
        schema=schema_text, question=question_text, semantic_md=semantic_md
    )
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    sbx = Path(tempfile.mkdtemp(prefix="codex-", dir=SANDBOX_ROOT))
    cmd = [
        "codex", "exec",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox", "read-only",
        "-c", 'web_search="disabled"',
        "-c", f'model_reasoning_effort="{reasoning_effort}"',
        "-m", model,
        "-",  # read prompt from stdin
    ]
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

    # Parse JSONL even on non-zero exit — turn.failed events tell us why.
    msg, tin, tout, fail = _parse_jsonl_events(proc.stdout)
    if proc.returncode != 0 and not msg:
        # Prefer structured fail reason over generic exit info.
        err_label = fail or f"codex exit {proc.returncode}"
        stderr_snippet = proc.stderr.decode("utf-8", errors="replace")[:200].strip()
        return ClaudeRawRunResult(
            sql=None,
            latency_ms_gen=latency,
            tokens_in=tin,
            tokens_out=tout,
            claude_error=(
                f"{err_label}; stderr: {stderr_snippet}" if stderr_snippet
                else err_label
            ),
            raw_stdout=proc.stdout.decode("utf-8", errors="replace")[:1000],
        )

    sql = strip_sql_fences(msg) if msg else ""
    if not sql:
        return ClaudeRawRunResult(
            sql=None,
            latency_ms_gen=latency,
            tokens_in=tin,
            tokens_out=tout,
            claude_error=fail or "codex produced no agent_message",
            raw_stdout=proc.stdout.decode("utf-8", errors="replace")[:1000],
        )
    return ClaudeRawRunResult(
        sql=sql,
        latency_ms_gen=latency,
        tokens_in=tin,
        tokens_out=tout,
        claude_error=None,
        raw_stdout="",
    )
