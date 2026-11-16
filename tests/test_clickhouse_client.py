"""Integration tests for ClickHouse engine. Require live .env and network."""
from __future__ import annotations

import os

import pytest

from cube.engines.clickhouse import ClickHouseError, make_clickhouse_engine
from cube.types import ExecResult

pytestmark = pytest.mark.skipif(
    not os.getenv("CLICKHOUSE_HOST"),
    reason="requires real ClickHouse env vars (.env)",
)


def test_execute_simple_count():
    engine = make_clickhouse_engine()
    result = engine.execute("SELECT count() FROM retail.DimProduct")
    assert isinstance(result, ExecResult)
    assert len(result.rows) == 1
    assert len(result.rows[0]) == 1
    assert int(result.rows[0][0]) > 0
    assert result.columns == ["count()"]
    assert result.exec_ms >= 0


def test_execute_enforces_row_cap():
    engine = make_clickhouse_engine()
    result = engine.execute("SELECT number FROM numbers(500)", row_cap=10)
    assert len(result.rows) == 10


def test_execute_invalid_sql_raises():
    engine = make_clickhouse_engine()
    with pytest.raises(ClickHouseError):
        engine.execute("SELECT not_a_column FROM retail.DimProduct")
