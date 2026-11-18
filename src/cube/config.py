"""Declarative YAML experiment configuration and environment resolution."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from cube.types import PromptMode

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env(repo_root: Path | None = None) -> None:
    """Load .env into os.environ (non-overriding)."""
    load_dotenv((repo_root or REPO_ROOT) / ".env", override=False)


def resolve_env(env_name: str | None, *, default: str | None = None) -> str | None:
    """Resolve an environment variable name after ensuring .env is loaded."""
    if not env_name:
        return default
    load_env()
    val = os.getenv(env_name)
    return val if val else default


def require_env(env_name: str) -> str:
    """Resolve a required environment variable, raising RuntimeError if missing."""
    val = resolve_env(env_name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {env_name}")
    return val


class ExperimentMeta(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class EngineConfig(BaseModel):
    type: Literal["clickhouse", "postgres"]
    dsn_env: str = "PG_ADMIN_DSN"
    exec_timeout_s: int = 30
    row_cap: int = 100


class DatasetConfig(BaseModel):
    path: Path
    schema_path: Path = Field(alias="schema")
    semantic_layer: Path | None = None
    only_ids: list[int] | None = None
    limit: int | None = None

    model_config = {"populate_by_name": True}

    def resolve(self, repo_root: Path = REPO_ROOT) -> DatasetConfig:
        def _abs(p: Path | None) -> Path | None:
            if p is None:
                return None
            return p if p.is_absolute() else (repo_root / p)

        return DatasetConfig(
            path=_abs(self.path),  # type: ignore[arg-type]
            schema=_abs(self.schema_path),  # type: ignore[arg-type]
            semantic_layer=_abs(self.semantic_layer),
            only_ids=self.only_ids,
            limit=self.limit,
        )


class ProviderConfig(BaseModel):
    base_url: str | None = None
    base_url_env: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    url: str | None = None
    url_env: str | None = None
    tool_id: str | None = None
    tool_id_env: str | None = None

    def get_base_url(self) -> str | None:
        return self.base_url or resolve_env(self.base_url_env)

    def get_api_key(self) -> str | None:
        return self.api_key or resolve_env(self.api_key_env)

    def get_url(self) -> str | None:
        return self.url or resolve_env(self.url_env)

    def get_tool_id(self) -> str | None:
        return self.tool_id or resolve_env(self.tool_id_env)


class SystemConfig(BaseModel):
    id: str
    label: str | None = None
    runner: Literal["openai_compatible", "agentic_local", "openwebui", "claude_cli", "codex_cli"]
    mode: PromptMode
    model: str
    dsn_env: str | None = None
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    params: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = 4

    @property
    def display_label(self) -> str:
        return self.label or self.id


class JudgeConfig(BaseModel):
    runner: Literal["openai_compatible", "claude_cli", "agy_cli"] = "claude_cli"
    model: str = "claude-sonnet-4-6"
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    params: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = 5
    row_preview_limit: int = 10


class LifecycleConfig(BaseModel):
    before_run: str | None = None
    after_run: str | None = None


class ExperimentSpec(BaseModel):
    experiment: ExperimentMeta
    engine: EngineConfig
    dataset: DatasetConfig
    systems: list[SystemConfig]
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)


def load_experiment(path: str | Path, *, repo_root: Path = REPO_ROOT) -> ExperimentSpec:
    """Load and validate an experiment YAML file."""
    load_env(repo_root)
    p = Path(path)
    if not p.is_absolute():
        p = repo_root / p
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    spec = ExperimentSpec.model_validate(raw)
    spec.dataset = spec.dataset.resolve(repo_root)
    return spec
