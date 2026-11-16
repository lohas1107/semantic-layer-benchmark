"""SQL fence stripping and extraction from LLM / agent responses."""
from __future__ import annotations

import re

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n?|\n?```\s*$", re.MULTILINE)
_DETAILS_RE = re.compile(r"<details\b[^>]*>.*?</details>", re.S | re.I)
_SQL_BLOCK_RE = re.compile(
    r"```(?:sql|postgresql|postgres|clickhouse)?[ \t]*\n(.*?)```",
    re.S | re.I,
)
_SQL_START_RE = re.compile(r"^\s*(with|select)\b", re.I)


def strip_sql_fences(s: str) -> str:
    """Remove ```sql / ``` fences and surrounding whitespace."""
    s = s.strip()
    if s.startswith("```"):
        s = _FENCE_RE.sub("", s).strip()
    return s


def extract_sql(text: str) -> str | None:
    """Extract the final SQL query from a single-shot or agentic response."""
    cleaned = _DETAILS_RE.sub("", text).strip()
    blocks = _SQL_BLOCK_RE.findall(cleaned)
    if blocks:
        sql = blocks[-1].strip()
        return sql or None
    stripped = strip_sql_fences(cleaned)
    if _SQL_START_RE.match(stripped):
        return stripped or None
    return None
