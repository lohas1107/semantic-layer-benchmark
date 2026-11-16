"""ClickHouse DatabaseEngine implementation."""
from __future__ import annotations

import os
import time
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import ClickHouseError as UpstreamError

from cube.config import load_env
from cube.engines.base import DatabaseError
from cube.types import ExecResult


class ClickHouseError(DatabaseError):
    """Raised when a ClickHouse SQL execution fails."""


class ClickHouseEngine:
    name = "clickhouse"
    dialect = "ClickHouse"

    def __init__(self, client: Client) -> None:
        self._client = client

    def execute(self, sql: str, *, timeout_s: int = 30, row_cap: int = 100) -> ExecResult:
        start = time.perf_counter()
        try:
            qr = self._client.query(
                sql,
                settings={
                    "max_execution_time": timeout_s,
                    "max_memory_usage": 10_000_000_000,
                    "max_bytes_before_external_group_by": 2_000_000_000,
                    "max_bytes_before_external_sort": 2_000_000_000,
                },
            )
        except UpstreamError as exc:
            raise ClickHouseError(str(exc)) from exc
        except Exception as exc:
            raise ClickHouseError(f"{type(exc).__name__}: {exc}") from exc

        exec_ms = int((time.perf_counter() - start) * 1000)
        rows: list[list[Any]] = [list(r) for r in qr.result_rows[:row_cap]]
        columns = list(qr.column_names)
        return ExecResult(rows=rows, columns=columns, exec_ms=exec_ms)

    def ping(self) -> bool:
        try:
            self.execute("SELECT 1")
            return True
        except ClickHouseError:
            return False

    def close(self) -> None:
        close_fn = getattr(self._client, "close", None)
        if callable(close_fn):
            close_fn()


def make_clickhouse_engine() -> ClickHouseEngine:
    load_env()
    required = ("CLICKHOUSE_HOST", "CLICKHOUSE_PORT", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD")
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Missing required ClickHouse env vars: {', '.join(missing)}")
    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ["CLICKHOUSE_PORT"]),
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        database=os.getenv("CLICKHOUSE_DATABASE", "retail"),
        secure=os.getenv("CLICKHOUSE_SECURE", "false").lower() in ("1", "true", "yes"),
        connect_timeout=15,
        send_receive_timeout=60,
    )
    return ClickHouseEngine(client)
