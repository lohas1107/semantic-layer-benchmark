"""Native Python agentic tool-calling runner using OpenAI-compatible API + DatabaseEngine."""
from __future__ import annotations

import json
import time
from typing import Any

import openai

from cube.engines.base import DatabaseEngine, DatabaseError
from cube.prompts.builder import build_prompts
from cube.prompts.sql_extract import extract_sql
from cube.types import RunnerRequest, RunnerResult

RUN_SQL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": (
            "Execute a read-only SQL SELECT query against the database and return "
            "the column names and up to 50 rows as JSON."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The read-only SQL SELECT query to execute.",
                }
            },
            "required": ["sql"],
        },
    },
}


class AgenticLocalRunner:
    name = "agentic_local"

    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_tool_rounds: int = 10,
        tool_row_cap: int = 50,
        tool_timeout_s: int = 30,
        extra_params: dict[str, Any] | None = None,
        client: openai.OpenAI | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_tool_rounds = max_tool_rounds
        self.tool_row_cap = tool_row_cap
        self.tool_timeout_s = tool_timeout_s
        self.extra_params = extra_params or {}
        self._client = client or openai.OpenAI(
            base_url=base_url,
            api_key=api_key or "EMPTY",
        )

    def _exec_tool(self, engine: DatabaseEngine, raw_args: str) -> str:
        try:
            args = json.loads(raw_args) if raw_args else {}
            sql = str(args.get("sql", "")).strip()
            if not sql:
                return json.dumps({"error": "missing 'sql' argument"})
            res = engine.execute(sql, timeout_s=self.tool_timeout_s, row_cap=self.tool_row_cap)
            return json.dumps(
                {
                    "columns": res.columns,
                    "rows": res.rows,
                    "row_count": len(res.rows),
                    "exec_ms": res.exec_ms,
                },
                default=str,
            )
        except DatabaseError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    def run(
        self,
        req: RunnerRequest,
        *,
        engine: DatabaseEngine | None = None,
    ) -> RunnerResult:
        if engine is None:
            raise ValueError("AgenticLocalRunner requires a DatabaseEngine instance")

        system_prompt, user_prompt = build_prompts(
            dialect=req.dialect,
            mode=req.mode,
            schema_text=req.schema_text,
            question_text=req.question.question,
            semantic_md=req.semantic_md,
            agentic=True,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tool_calls_log: list[dict[str, Any]] = []
        total_tin = 0
        total_tout = 0
        rounds = 0
        final_content = ""
        start = time.perf_counter()

        for _ in range(self.max_tool_rounds):
            rounds += 1
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=[RUN_SQL_TOOL],  # type: ignore[arg-type]
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    timeout=req.timeout_s,
                    **self.extra_params,
                )
            except Exception as exc:
                latency = int((time.perf_counter() - start) * 1000)
                is_infra = isinstance(exc, (openai.APIConnectionError, openai.InternalServerError))
                return RunnerResult(
                    sql=None,
                    raw_response=final_content,
                    latency_ms_gen=latency,
                    tokens_in=total_tin,
                    tokens_out=total_tout,
                    tool_calls=tool_calls_log,
                    rounds=rounds,
                    error=f"{type(exc).__name__}: {exc}"[:1000],
                    infra_error=is_infra,
                )

            if resp.usage:
                total_tin += int(getattr(resp.usage, "prompt_tokens", 0) or 0)
                total_tout += int(getattr(resp.usage, "completion_tokens", 0) or 0)

            msg = resp.choices[0].message
            final_content = msg.content or ""
            tcalls = getattr(msg, "tool_calls", None) or []

            if not tcalls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": final_content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tcalls
                    ],
                }
            )
            for tc in tcalls:
                if tc.function.name == "run_sql":
                    output = self._exec_tool(engine, tc.function.arguments)
                else:
                    output = json.dumps({"error": f"unknown tool: {tc.function.name}"})
                tool_calls_log.append(
                    {
                        "call_id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                        "output": output,
                    }
                )
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": output})

        latency = int((time.perf_counter() - start) * 1000)
        sql = extract_sql(final_content)
        err = None if sql else "no final SQL in answer"
        return RunnerResult(
            sql=sql,
            raw_response=final_content,
            latency_ms_gen=latency,
            tokens_in=total_tin,
            tokens_out=total_tout,
            tool_calls=tool_calls_log,
            rounds=rounds,
            error=err,
        )
