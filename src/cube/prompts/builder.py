"""Unified prompt builder across dialects, prompt modes, and single-shot/agentic."""
from __future__ import annotations

from cube.types import PromptMode

_DIALECT_HINTS = {
    "ClickHouse": (
        "Prefer ClickHouse-native functions (toYear, toMonth, toQuarter, sum, count, etc.)."
    ),
    "PostgreSQL": "Prefer PostgreSQL-native functions (EXTRACT, date_trunc, sum, count, etc.).",
}

_SINGLE_SHOT_RULES = (
    "Output only SQL — no markdown fences, no commentary, no explanation."
)

_AGENTIC_RULES = (
    "You can call the `run_sql` tool to execute read-only SQL against the "
    "database and inspect the results, as many times as you need. When you are "
    "done, reply with exactly one final SQL SELECT query that answers the "
    "question, inside a single ```sql code block, and nothing else. The final "
    "query is executed on its own, so it must be self-contained."
)


def build_prompts(
    *,
    dialect: str,
    mode: PromptMode,
    schema_text: str,
    question_text: str,
    semantic_md: str | None = None,
    agentic: bool = False,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the requested dialect, mode, and style."""
    hint = _DIALECT_HINTS.get(dialect, "")
    rules = _AGENTIC_RULES if agentic else _SINGLE_SHOT_RULES

    if mode == "raw":
        system_prompt = (
            f"You are a {dialect} SQL expert. Given the schema below, write a single "
            f"SQL SELECT query that answers the user's question. {rules} {hint}"
        ).strip()
        user_prompt = f"{schema_text}\n\nQuestion: {question_text}"
        return system_prompt, user_prompt

    if not semantic_md:
        raise ValueError("semantic_md is required when mode='semantic'")

    system_prompt = (
        f"You are a {dialect} SQL expert. Given (1) a SEMANTIC LAYER markdown that "
        "explains the business meaning of tables, columns, and calculated metrics, "
        "and (2) the raw DDL for the schema, write a single SQL SELECT query that "
        f"answers the user's question. {rules} {hint} Follow the semantic-layer "
        "guidance: it supplies business context that is NOT derivable from the DDL alone."
    ).strip()
    user_prompt = (
        "# SEMANTIC LAYER\n\n"
        f"{semantic_md}\n\n"
        "# RAW DDL (authoritative for types)\n\n"
        f"{schema_text}\n\n"
        f"# Question: {question_text}"
    )
    return system_prompt, user_prompt
