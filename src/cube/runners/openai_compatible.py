"""Single-shot OpenAI-compatible runner (OpenAI, vLLM, Nebius, OpenRouter, etc.)."""
from __future__ import annotations

import time
from typing import Any

import openai

from cube.engines.base import DatabaseEngine
from cube.prompts.builder import build_prompts
from cube.prompts.sql_extract import extract_sql
from cube.types import RunnerRequest, RunnerResult


class OpenAICompatibleRunner:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        extra_params: dict[str, Any] | None = None,
        client: openai.OpenAI | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_params = extra_params or {}
        self._client = client or openai.OpenAI(
            base_url=base_url,
            api_key=api_key or "EMPTY",
        )

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
        start = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
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
                raw_response="",
                latency_ms_gen=latency,
                error=f"{type(exc).__name__}: {exc}"[:1000],
                infra_error=is_infra,
            )

        latency = int((time.perf_counter() - start) * 1000)
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = resp.usage
        tin = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        tout = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        sql = extract_sql(content)
        err = None if sql else "no SQL extracted from response"
        return RunnerResult(
            sql=sql,
            raw_response=content,
            latency_ms_gen=latency,
            tokens_in=tin,
            tokens_out=tout,
            error=err,
        )
