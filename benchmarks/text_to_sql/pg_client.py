"""PostgreSQL counterpart of clickhouse_client: same execute() contract.

Every statement runs in a READ ONLY transaction with a statement_timeout, and
values are normalised to JSON-friendly Python types (Decimal -> float,
date/datetime -> ISO string) so results compare like the ClickHouse ones.
"""
from __future__ import annotations

import datetime as dt
import os
import time
from decimal import Decimal
from typing import Any

import psycopg

from benchmarks.text_to_sql.clickhouse_client import ExecResult
from benchmarks.text_to_sql.config import REPO_ROOT


class PostgresError(RuntimeError):
    """Raised when a SQL execution fails."""


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


class BenchmarkPostgresClient:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def execute(self, sql: str, *, timeout_s: int = 30, row_cap: int = 100) -> ExecResult:
        """Run sql read-only, return ExecResult with at most row_cap rows.

        Raises PostgresError on any failure (invalid SQL, timeout, connection).
        """
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


def make_pg_client(dsn: str | None = None) -> BenchmarkPostgresClient:
    if dsn is None:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env", override=False)
        dsn = os.getenv("PG_DSN")
    if not dsn:
        raise RuntimeError("Missing required env var: PG_DSN")
    return BenchmarkPostgresClient(dsn)
