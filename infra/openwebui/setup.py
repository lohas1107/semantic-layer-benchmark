"""Idempotently configure Open WebUI for the agentic benchmark.

1. Ensure an OpenAI-compatible connection to the Nebius vLLM endpoint exists
   (only when NEBIUS_BASE_URL and NEBIUS_TOKEN are set).
2. Create or update the self-built Workspace Tools `run_sql_admin` and
   `run_sql_marketing` from infra/openwebui/run_sql_tool.py and set their
   PG_DSN valves.

Env: OPENWEBUI_URL, OPENWEBUI_API_KEY (admin),
     OPENWEBUI_RUN_SQL_ADMIN_TOOL_ID, OPENWEBUI_PG_ADMIN_DSN,
     OPENWEBUI_RUN_SQL_MARKETING_TOOL_ID, OPENWEBUI_PG_MARKETING_DSN,
     NEBIUS_BASE_URL (…/v1), NEBIUS_TOKEN, OPENWEBUI_MODEL (enables its usage capability).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
import re

import httpx
from dotenv import load_dotenv

from cube.config import REPO_ROOT

TOOL_FILE = REPO_ROOT / "infra" / "openwebui" / "run_sql_tool.py"
DEFAULT_ADMIN_DSN = "postgresql://admin:admin@contoso-pg:5432/contoso"
DEFAULT_MARKETING_DSN = "postgresql://marketing:marketing@contoso-pg:5432/contoso"
NEBIUS_TUNNEL_RE = re.compile(r"^https://[^/]+\.tunnel\.applications\.[^/]+\.nebius\.cloud(/|$)")


def ensure_connection(http: httpx.Client, base_url: str, token: str) -> str:
    cfg = http.get("/openai/config").raise_for_status().json()
    urls = list(cfg.get("OPENAI_API_BASE_URLS") or [])
    keys = list(cfg.get("OPENAI_API_KEYS") or [])
    configs = dict(cfg.get("OPENAI_API_CONFIGS") or {})
    base_url = base_url.rstrip("/")
    if base_url in urls:
        idx = urls.index(base_url)
        if keys[idx] == token and cfg.get("ENABLE_OPENAI_API"):
            return "connection unchanged"
        keys[idx] = token
    else:
        # A recreated endpoint gets a new tunnel URL: replace the old Nebius entry
        # instead of leaving a dead connection behind.
        old = [i for i, u in enumerate(urls) if NEBIUS_TUNNEL_RE.match(u)]
        if old:
            idx = old[0]
            urls[idx], keys[idx] = base_url, token
        else:
            urls.append(base_url)
            keys.append(token)
            idx = len(urls) - 1
    configs.setdefault(str(idx), {"enable": True, "tags": [{"name": "nebius"}]})
    http.post(
        "/openai/config/update",
        json={"ENABLE_OPENAI_API": True, "OPENAI_API_BASE_URLS": urls,
              "OPENAI_API_KEYS": keys, "OPENAI_API_CONFIGS": configs},
    ).raise_for_status()
    return f"connection set at index {idx}"


def ensure_tool(http: httpx.Client, tool_id: str, dsn: str) -> str:
    content = TOOL_FILE.read_text(encoding="utf-8")
    form = {
        "id": tool_id,
        "name": "run_sql",
        "content": content,
        "meta": {"description": "Read-only SQL on the Contoso PostgreSQL benchmark DB",
                 "manifest": {}},
        "commit_message": "sync from infra/openwebui/run_sql_tool.py",
    }
    existing = http.get(f"/api/v1/tools/id/{tool_id}")
    if existing.status_code == 200:
        if existing.json().get("content") == content:
            action = "tool unchanged"
        else:
            http.post(f"/api/v1/tools/id/{tool_id}/update", json=form).raise_for_status()
            action = "tool updated"
    else:
        http.post("/api/v1/tools/create", json=form).raise_for_status()
        action = "tool created"
    valves = http.get(f"/api/v1/tools/id/{tool_id}/valves").raise_for_status().json() or {}
    if valves.get("PG_DSN") != dsn:
        valves["PG_DSN"] = dsn
        http.post(f"/api/v1/tools/id/{tool_id}/valves/update", json=valves).raise_for_status()
        action += ", PG_DSN valve set"
    return action


def ensure_usage_capability(http: httpx.Client, model_id: str) -> str:
    """Turn on the model's `usage` capability so Open WebUI asks the provider for
    token counts (stream_options.include_usage); otherwise message.usage is empty.
    Done as a base-model override with empty params: no system prompt or other
    setting changes, and only this model is affected."""
    r = http.get("/api/v1/models/model", params={"id": model_id})
    if r.status_code == 200 and r.json():
        m = r.json()
        meta = m.get("meta") or {}
        caps = meta.get("capabilities") or {}
        if caps.get("usage"):
            return "usage capability unchanged"
        meta["capabilities"] = {**caps, "usage": True}
        form = {"id": model_id, "base_model_id": m.get("base_model_id"), "name": m.get("name"),
                "meta": meta, "params": m.get("params") or {}}
        http.post("/api/v1/models/model/update", json=form).raise_for_status()
        return "usage capability enabled (override updated)"
    form = {"id": model_id, "base_model_id": None, "name": model_id,
            "meta": {"capabilities": {"usage": True}}, "params": {}}
    http.post("/api/v1/models/create", json=form).raise_for_status()
    return "usage capability enabled (override created)"


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    api_key = os.getenv("OPENWEBUI_API_KEY")
    if not api_key:
        print("OPENWEBUI_API_KEY is not set", file=sys.stderr)
        return 1
    http = httpx.Client(
        base_url=os.getenv("OPENWEBUI_URL", "http://localhost:3000").rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    nb_url, nb_token = os.getenv("NEBIUS_BASE_URL"), os.getenv("NEBIUS_TOKEN")
    if nb_url and nb_token:
        print(ensure_connection(http, nb_url, nb_token))
    print(ensure_tool(
        http,
        os.getenv("OPENWEBUI_RUN_SQL_ADMIN_TOOL_ID", "run_sql_admin"),
        os.getenv("OPENWEBUI_PG_ADMIN_DSN", DEFAULT_ADMIN_DSN),
    ))
    print(ensure_tool(
        http,
        os.getenv("OPENWEBUI_RUN_SQL_MARKETING_TOOL_ID", "run_sql_marketing"),
        os.getenv("OPENWEBUI_PG_MARKETING_DSN", DEFAULT_MARKETING_DSN),
    ))
    model_id = os.getenv("OPENWEBUI_MODEL")
    if model_id:
        print(ensure_usage_capability(http, model_id))
        # Open WebUI caches model info in app.state.MODELS; listing models refreshes
        # it, otherwise a changed capability is ignored until something else does.
        http.get("/api/models").raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
