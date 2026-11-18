"""
title: run_sql
description: Execute a read-only SQL query against the Contoso PostgreSQL benchmark database.
version: 1.0.0
license: MIT
"""
# Open WebUI Workspace Tool (self-built; Open WebUI has no native SQL tool).
# Installed/updated by scripts/openwebui_setup.py. Runs inside the Open WebUI
# server process, which already ships psycopg 3.

import json
from decimal import Decimal

import psycopg
from pydantic import BaseModel, Field


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


class Tools:
    class Valves(BaseModel):
        PG_DSN: str = Field(
            default="postgresql://admin:admin@contoso-pg:5432/contoso",
            description="libpq connection string (use a read-only role)",
        )
        ROW_LIMIT: int = Field(default=20, description="Max rows returned to the model")
        TIMEOUT_S: int = Field(default=30, description="statement_timeout in seconds")
        MAX_CHARS: int = Field(default=6000, description="Truncate the JSON result to this")

    def __init__(self):
        self.valves = self.Valves()

    def run_sql(self, query: str) -> str:
        """
        Execute one read-only SQL query on the PostgreSQL database and return the result.
        Use it to explore tables, check values and verify your query before answering.
        :param query: A single PostgreSQL SELECT (or WITH ... SELECT) statement.
        :return: JSON with columns, the first rows, the total row count, or an error.
        """
        v = self.valves
        try:
            with psycopg.connect(v.PG_DSN, connect_timeout=10) as conn:
                conn.read_only = True
                with conn.cursor() as cur:
                    cur.execute(f"SET LOCAL statement_timeout = {int(v.TIMEOUT_S) * 1000}")
                    cur.execute(query)
                    if cur.description is None:
                        return json.dumps({"error": "statement returned no result set"})
                    columns = [d.name for d in cur.description]
                    rows = cur.fetchmany(v.ROW_LIMIT + 1)
                    truncated = len(rows) > v.ROW_LIMIT
                    rows = rows[: v.ROW_LIMIT]
                    total = cur.rowcount
                conn.rollback()
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {str(exc).strip()}"[:1500]})
        out = json.dumps(
            {
                "columns": columns,
                "rows": [[_jsonable(x) for x in r] for r in rows],
                "row_count": total,
                "truncated": truncated,
            },
            default=str,
        )
        if len(out) > v.MAX_CHARS:
            out = out[: v.MAX_CHARS] + '... (truncated)"'
        return out
