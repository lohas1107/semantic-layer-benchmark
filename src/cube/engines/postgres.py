"""PostgreSQL DatabaseEngine implementation (read-only transactions)."""
from __future__ import annotations

import datetime as dt
import time
from decimal import Decimal
from typing import Any

import psycopg

from cube.config import require_env
from cube.engines.base import DatabaseError
from cube.types import ExecResult


class PostgresError(DatabaseError):
    """Raised when a PostgreSQL SQL execution fails."""


def normalise_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, memoryview | bytes):
        return bytes(v).hex()
    return v


class PostgresEngine:
    name = "postgres"
    dialect = "PostgreSQL"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def execute(self, sql: str, *, timeout_s: int = 30, row_cap: int = 100) -> ExecResult:
        start = time.perf_counter()
        try:
            with psycopg.connect(self._dsn, connect_timeout=15) as conn:
                conn.read_only = True
                with conn.cursor() as cur:
                    cur.execute(f"SET LOCAL statement_timeout = {int(timeout_s) * 1000}")
                    cur.execute(sql)
                    if cur.description is None:
                        raise PostgresError("statement returned no result set")
                    columns = [d.name for d in cur.description]
                    raw = cur.fetchmany(row_cap)
                conn.rollback()
        except PostgresError:
            raise
        except psycopg.Error as exc:
            raise PostgresError(str(exc).strip()) from exc
        except Exception as exc:
            raise PostgresError(f"{type(exc).__name__}: {exc}") from exc

        rows = [[normalise_value(v) for v in r] for r in raw]
        exec_ms = int((time.perf_counter() - start) * 1000)
        return ExecResult(rows=rows, columns=columns, exec_ms=exec_ms)

    def ping(self) -> bool:
        try:
            self.execute("SELECT 1")
            return True
        except PostgresError:
            return False

    def close(self) -> None:
        return None


def make_postgres_engine(
    *, dsn_env: str = "PG_ADMIN_DSN", dsn: str | None = None
) -> PostgresEngine:
    resolved = dsn or require_env(dsn_env)
    return PostgresEngine(resolved)
