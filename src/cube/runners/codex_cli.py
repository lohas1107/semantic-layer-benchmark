"""Codex CLI runner (`codex exec --json`)."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time

from cube.engines.base import DatabaseEngine
from cube.prompts.builder import build_prompts
from cube.prompts.sql_extract import strip_sql_fences
from cube.types import RunnerRequest, RunnerResult


def _parse_jsonl_events(stdout: bytes) -> tuple[str | None, int, int, str | None]:
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


class CodexCLIRunner:
    name = "codex_cli"

    def __init__(
        self,
        *,
        model: str = "gpt-5.4",
        reasoning_effort: str = "medium",
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort

    def run(
        self,
        req: RunnerRequest,
        *,
        engine: DatabaseEngine | None = None,
    ) -> RunnerResult:
        system_prompt, user_prompt = build_prompts(
            dialect=req.dialect,
            mode=req.mode,
            schema_text=req.schema_text,
            question_text=req.question.question,
            semantic_md=req.semantic_md,
            agentic=False,
        )
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        cmd = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-c",
            'web_search="disabled"',
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "-m",
            self.model,
            "-",
        ]
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="cube-codex-") as sbx:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=sbx,
                    input=full_prompt.encode("utf-8"),
                    capture_output=True,
                    timeout=req.timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                return RunnerResult(
                    sql=None,
                    raw_response="",
                    latency_ms_gen=int((time.perf_counter() - start) * 1000),
                    error=f"timeout after {req.timeout_s}s: {exc}",
                )

        latency = int((time.perf_counter() - start) * 1000)
        msg, tin, tout, fail = _parse_jsonl_events(proc.stdout)
        raw_stdout = proc.stdout.decode("utf-8", errors="replace")[:1000]

        if proc.returncode != 0 and not msg:
            err_label = fail or f"codex exit {proc.returncode}"
            stderr_snip = proc.stderr.decode("utf-8", errors="replace")[:200].strip()
            return RunnerResult(
                sql=None,
                raw_response=raw_stdout,
                latency_ms_gen=latency,
                tokens_in=tin,
                tokens_out=tout,
                error=f"{err_label}; stderr: {stderr_snip}" if stderr_snip else err_label,
            )

        sql = strip_sql_fences(msg) if msg else ""
        if not sql:
            return RunnerResult(
                sql=None,
                raw_response=raw_stdout,
                latency_ms_gen=latency,
                tokens_in=tin,
                tokens_out=tout,
                error=fail or "codex produced no agent_message",
            )

        return RunnerResult(
            sql=sql,
            raw_response=msg or "",
            latency_ms_gen=latency,
            tokens_in=tin,
            tokens_out=tout,
        )
