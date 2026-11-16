"""Claude CLI runner (`claude -p`), supporting both raw and semantic modes."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import dataclass

from cube.engines.base import DatabaseEngine
from cube.prompts.builder import build_prompts
from cube.prompts.sql_extract import strip_sql_fences
from cube.types import RunnerRequest, RunnerResult


@dataclass(frozen=True)
class ClaudeCLIParseResult:
    sql: str
    tokens_in: int
    tokens_out: int
    is_error: bool


def parse_claude_cli_output(stdout: bytes) -> ClaudeCLIParseResult:
    obj = json.loads(stdout.decode("utf-8", errors="replace"))
    raw_result = obj.get("result", "") or ""
    usage = obj.get("usage") or {}
    tokens_in_total = (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
    )
    return ClaudeCLIParseResult(
        sql=strip_sql_fences(raw_result),
        tokens_in=tokens_in_total,
        tokens_out=int(usage.get("output_tokens", 0) or 0),
        is_error=bool(obj.get("is_error", False)),
    )


class ClaudeCLIRunner:
    name = "claude_cli"

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-7",
        effort: str = "medium",
        max_budget_usd: float = 2.0,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_budget_usd = max_budget_usd

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
        cmd = [
            "claude",
            "-p",
            "--effort",
            self.effort,
            "--model",
            self.model,
            "--tools",
            "",
            "--system-prompt",
            system_prompt,
            "--disable-slash-commands",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--max-budget-usd",
            str(self.max_budget_usd),
        ]
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="cube-claude-") as sbx:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=sbx,
                    input=user_prompt.encode("utf-8"),
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
        raw_stdout = proc.stdout.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            stderr_snip = proc.stderr.decode("utf-8", errors="replace")[:500]
            return RunnerResult(
                sql=None,
                raw_response=raw_stdout,
                latency_ms_gen=latency,
                error=f"claude exit {proc.returncode}; stderr: {stderr_snip}",
            )

        try:
            parsed = parse_claude_cli_output(proc.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            return RunnerResult(
                sql=None,
                raw_response=raw_stdout,
                latency_ms_gen=latency,
                error=f"invalid claude output: {exc}",
            )

        if parsed.is_error:
            return RunnerResult(
                sql=None,
                raw_response=raw_stdout,
                latency_ms_gen=latency,
                tokens_in=parsed.tokens_in,
                tokens_out=parsed.tokens_out,
                error="claude returned is_error=true",
            )

        return RunnerResult(
            sql=parsed.sql or None,
            raw_response=raw_stdout,
            latency_ms_gen=latency,
            tokens_in=parsed.tokens_in,
            tokens_out=parsed.tokens_out,
            error=None if parsed.sql else "empty SQL from claude",
        )
