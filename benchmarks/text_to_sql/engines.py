"""Per-engine benchmark assets: question file, DDL, semantic layer, DB client.

`clickhouse` is the original Cube setup and stays the default. `postgres`
uses the PG port (docker image lohas1107/contoso:pg-cube-*).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.text_to_sql.config import REPO_ROOT


@dataclass(frozen=True)
class Engine:
    name: str
    benchmark_json: Path
    schema_fixture: Path
    semantic_fixture: Path
    make_client: Callable[[], Any]
    error: type[Exception]


def _clickhouse() -> Engine:
    from benchmarks.text_to_sql.clickhouse_client import ClickHouseError, make_client

    return Engine(
        name="clickhouse",
        benchmark_json=REPO_ROOT / "text_to_sql_benchmark.json",
        schema_fixture=REPO_ROOT / "fixtures" / "retail_schema.sql",
        semantic_fixture=REPO_ROOT / "fixtures" / "semantic_layer.md",
        make_client=make_client,
        error=ClickHouseError,
    )


def _postgres() -> Engine:
    from benchmarks.text_to_sql.pg_client import PostgresError, make_pg_client

    return Engine(
        name="postgres",
        benchmark_json=REPO_ROOT / "text_to_sql_benchmark.pg.json",
        schema_fixture=REPO_ROOT / "fixtures" / "retail_schema.pg.sql",
        semantic_fixture=REPO_ROOT / "fixtures" / "semantic_layer.pg.md",
        make_client=make_pg_client,
        error=PostgresError,
    )


ENGINES = {"clickhouse": _clickhouse, "postgres": _postgres}


def get_engine(name: str) -> Engine:
    try:
        return ENGINES[name]()
    except KeyError:
        raise ValueError(f"unknown engine {name!r}; choose from {sorted(ENGINES)}") from None
