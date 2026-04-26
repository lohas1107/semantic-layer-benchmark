"""Thin wrapper over clickhouse-connect for benchmark SQL execution."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import ClickHouseError as UpstreamError

from benchmarks.text_to_sql.config import Config, load_config


class ClickHouseError(RuntimeError):
    """Raised when a SQL execution fails."""


@dataclass(frozen=True)
class ExecResult:
    rows: list[list[Any]]
    columns: list[str]
    exec_ms: int


class BenchmarkClickHouseClient:
    def __init__(self, client: Client) -> None:
        self._client = client

    def execute(self, sql: str, *, timeout_s: int = 30, row_cap: int = 100) -> ExecResult:
        """Run sql, return ExecResult with at most row_cap rows.

        Raises ClickHouseError on any upstream failure (invalid SQL, network,
        timeout). Row cap is enforced client-side by slicing the returned rows.
        """
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

        rows = [list(r) for r in qr.result_rows[:row_cap]]
        columns = list(qr.column_names)
        exec_ms = int((time.perf_counter() - start) * 1000)
        return ExecResult(rows=rows, columns=columns, exec_ms=exec_ms)

    def ping(self) -> bool:
        """Quick health check."""
        try:
            self._client.command("SELECT 1")
            return True
        except Exception:
            return False


def make_client(cfg: Config | None = None) -> BenchmarkClickHouseClient:
    cfg = cfg or load_config()
    raw = clickhouse_connect.get_client(
        host=cfg.clickhouse_host,
        port=cfg.clickhouse_port,
        username=cfg.clickhouse_user,
        password=cfg.clickhouse_password,
        secure=cfg.clickhouse_secure,
        verify=cfg.clickhouse_verify,
        connect_timeout=30,
        send_receive_timeout=60,
    )
    return BenchmarkClickHouseClient(raw)
