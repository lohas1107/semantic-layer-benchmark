"""Database engine registry."""
from __future__ import annotations

from cube.config import EngineConfig
from cube.engines.base import DatabaseEngine, DatabaseError
from cube.engines.clickhouse import ClickHouseEngine, ClickHouseError, make_clickhouse_engine
from cube.engines.postgres import PostgresEngine, PostgresError, make_postgres_engine


def create_engine(cfg: EngineConfig) -> DatabaseEngine:
    if cfg.type == "clickhouse":
        return make_clickhouse_engine()
    if cfg.type == "postgres":
        return make_postgres_engine(dsn_env=cfg.dsn_env)
    raise ValueError(f"Unsupported engine type: {cfg.type!r}")


__all__ = [
    "ClickHouseEngine",
    "ClickHouseError",
    "DatabaseEngine",
    "DatabaseError",
    "PostgresEngine",
    "PostgresError",
    "create_engine",
]
