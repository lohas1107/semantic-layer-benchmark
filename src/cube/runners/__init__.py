"""Runner registry."""
from __future__ import annotations

from cube.config import SystemConfig, require_env
from cube.runners.agentic_local import AgenticLocalRunner
from cube.runners.base import Runner
from cube.runners.claude_cli import ClaudeCLIRunner
from cube.runners.codex_cli import CodexCLIRunner
from cube.runners.openai_compatible import OpenAICompatibleRunner
from cube.runners.openwebui import OpenWebUIRunner


def create_runner(cfg: SystemConfig) -> Runner:
    p = cfg.params
    if cfg.runner == "openai_compatible":
        extra = {
            k: v
            for k, v in p.items()
            if k not in ("temperature", "max_tokens", "timeout_s")
        }
        return OpenAICompatibleRunner(
            model=cfg.model,
            base_url=cfg.provider.get_base_url(),
            api_key=cfg.provider.get_api_key(),
            temperature=float(p.get("temperature", 0.0)),
            max_tokens=int(p.get("max_tokens", 4096)),
            extra_params=extra,
        )
    if cfg.runner == "agentic_local":
        _known = (
            "temperature",
            "max_tokens",
            "timeout_s",
            "max_tool_rounds",
            "tool_row_cap",
            "tool_timeout_s",
        )
        extra = {k: v for k, v in p.items() if k not in _known}
        return AgenticLocalRunner(
            model=cfg.model,
            base_url=cfg.provider.get_base_url(),
            api_key=cfg.provider.get_api_key(),
            temperature=float(p.get("temperature", 0.0)),
            max_tokens=int(p.get("max_tokens", 4096)),
            max_tool_rounds=int(p.get("max_tool_rounds", 10)),
            tool_row_cap=int(p.get("tool_row_cap", 50)),
            tool_timeout_s=int(p.get("tool_timeout_s", 30)),
            extra_params=extra,
        )
    if cfg.runner == "openwebui":
        base_url = cfg.provider.get_url() or require_env("OPENWEBUI_URL")
        api_key = cfg.provider.get_api_key() or require_env("OPENWEBUI_API_KEY")
        tool_id = cfg.provider.get_tool_id() or "run_sql"
        return OpenWebUIRunner(
            model=cfg.model,
            base_url=base_url,
            api_key=api_key,
            tool_id=tool_id,
            timeout_s=float(p.get("timeout_s", 300.0)),
            keep_chats=bool(p.get("keep_chats", False)),
        )
    if cfg.runner == "claude_cli":
        return ClaudeCLIRunner(
            model=cfg.model,
            effort=str(p.get("effort", "medium")),
            max_budget_usd=float(p.get("max_budget_usd", 2.0)),
        )
    if cfg.runner == "codex_cli":
        return CodexCLIRunner(
            model=cfg.model,
            reasoning_effort=str(p.get("reasoning_effort", "medium")),
        )
    raise ValueError(f"Unknown runner: {cfg.runner!r}")


__all__ = [
    "AgenticLocalRunner",
    "ClaudeCLIRunner",
    "CodexCLIRunner",
    "OpenAICompatibleRunner",
    "OpenWebUIRunner",
    "Runner",
    "create_runner",
]
