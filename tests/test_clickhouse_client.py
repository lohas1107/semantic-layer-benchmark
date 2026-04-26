"""Integration tests for ClickHouse client. Require live .env and network."""
from __future__ import annotations

import os

import pytest

from benchmarks.text_to_sql.clickhouse_client import ClickHouseError, ExecResult, make_client

pytestmark = pytest.mark.skipif(
    not os.getenv("CLICKHOUSE_HOST"),
    reason="requires real ClickHouse env vars (.env)",
)


def test_execute_simple_count():
    client = make_client()
    result = client.execute("SELECT count() FROM retail.DimProduct")
    assert isinstance(result, ExecResult)
    assert len(result.rows) == 1
    assert len(result.rows[0]) == 1
    assert int(result.rows[0][0]) > 0
    assert result.columns == ["count()"]
    assert result.exec_ms >= 0


def test_execute_enforces_row_cap():
    client = make_client()
    result = client.execute("SELECT number FROM numbers(500)", row_cap=10)
    assert len(result.rows) == 10


def test_execute_invalid_sql_raises():
    client = make_client()
    with pytest.raises(ClickHouseError):
        client.execute("SELECT not_a_column FROM retail.DimProduct")
