"""Environment-backed configuration for the benchmark."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

_TRUTHY = {"true", "1", "yes", "y", "on"}


def _parse_bool(val: str | None, *, default: bool) -> bool:
    if val is None or val == "":
        return default
    return val.strip().lower() in _TRUTHY


def _require(name: str) -> str:
    val = os.getenv(name)
    if val is None or val == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return val


@dataclass(frozen=True)
class Config:
    clickhouse_host: str
    clickhouse_port: int
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_secure: bool
    clickhouse_verify: bool
    repo_root: Path


def load_config(env_path: Path | None = None) -> Config:
    """Load env vars (optionally from .env), validate, return Config."""
    if env_path is not None:
        load_dotenv(env_path, override=False)
    else:
        load_dotenv(REPO_ROOT / ".env", override=False)

    return Config(
        clickhouse_host=_require("CLICKHOUSE_HOST"),
        clickhouse_port=int(_require("CLICKHOUSE_PORT")),
        clickhouse_user=_require("CLICKHOUSE_USER"),
        clickhouse_password=_require("CLICKHOUSE_PASSWORD"),
        clickhouse_secure=_parse_bool(os.getenv("CLICKHOUSE_SECURE"), default=True),
        clickhouse_verify=_parse_bool(os.getenv("CLICKHOUSE_VERIFY"), default=True),
        repo_root=REPO_ROOT,
    )
