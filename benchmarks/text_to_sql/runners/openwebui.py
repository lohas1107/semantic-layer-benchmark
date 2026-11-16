"""Open WebUI agentic runner: the model answers through Open WebUI's native
tool loop, calling the self-built `run_sql` Workspace Tool against PostgreSQL.

Flow per question (Open WebUI v0.13.3):
  1. POST /api/v1/chats/new                 -> saved chat id
  2. POST /api/chat/completions             stream=true + chat_id + id + tool_ids
     Tools only execute on the streaming path with an event emitter, which needs
     both chat_id and the assistant message id. No session_id, so no built-in
     tools (knowledge, notes, web, ...) are injected: run_sql is the only tool.
  3. GET  /api/v1/chats/{chat_id}           -> final content, output items, usage
  4. DELETE /api/v1/chats/{chat_id}         (unless keep_chat)

The final answer must contain one ```sql block; the harness re-executes that SQL
itself so scoring matches the single-shot runners.
"""
from __future__ import annotations

import contextlib
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from benchmarks.text_to_sql.runners.claude_raw import build_user_prompt, strip_sql_fences
from benchmarks.text_to_sql.runners.claude_semantic import build_semantic_user_prompt

_AGENT_RULES = (
    "You can call the `run_sql` tool to execute read-only SQL against the "
    "database and inspect the results, as many times as you need. When you are "
    "done, reply with exactly one final SQL SELECT query that answers the "
    "question, inside a single ```sql code block, and nothing else. The final "
    "query is executed on its own, so it must be self-contained. Prefer "
    "PostgreSQL-native functions (EXTRACT, date_trunc, sum, count, etc.)."
)

OPENWEBUI_RAW_SYSTEM_PROMPT = (
    "You are a PostgreSQL SQL expert. Given the schema below, answer the user's "
    "question with a single SQL SELECT query. " + _AGENT_RULES
)

OPENWEBUI_SEMANTIC_SYSTEM_PROMPT = (
    "You are a PostgreSQL SQL expert. Given (1) a SEMANTIC LAYER markdown that "
    "explains the business meaning of tables, columns, and calculated metrics, "
    "and (2) the raw DDL for the schema, answer the user's question with a "
    "single SQL SELECT query. Follow the semantic-layer guidance: it supplies "
    "business context that is NOT derivable from the DDL alone. " + _AGENT_RULES
)

_DETAILS_RE = re.compile(r"<details\b[^>]*>.*?</details>", re.S | re.I)
_SQL_BLOCK_RE = re.compile(r"```(?:sql|postgresql|postgres)?[ \t]*\n(.*?)```", re.S | re.I)
_SQL_START_RE = re.compile(r"^\s*(with|select)\b", re.I)


@dataclass
class OpenWebUIRunResult:
    sql: str | None
    final_content: str
    latency_ms_gen: int
    tokens_in: int
    tokens_out: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    error: str | None = None
    infra_error: bool = False
    attempts: int = 1


def build_prompts(
    *, schema_text: str, question_text: str, semantic_md: str | None
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt), mirroring the Claude runners."""
    if semantic_md is None:
        return OPENWEBUI_RAW_SYSTEM_PROMPT, build_user_prompt(schema_text, question_text)
    return OPENWEBUI_SEMANTIC_SYSTEM_PROMPT, build_semantic_user_prompt(
        semantic_md=semantic_md, schema_text=schema_text, question_text=question_text
    )


def visible_content(content: str) -> str:
    """Drop Open WebUI's <details> blocks (reasoning, tool calls) from content."""
    return _DETAILS_RE.sub("", content or "").strip()


def extract_final_sql(content: str) -> str | None:
    """Last fenced SQL block of the final answer; else the bare answer if it is SQL."""
    text = visible_content(content)
    blocks = [b.strip() for b in _SQL_BLOCK_RE.findall(text) if b.strip()]
    if blocks:
        return blocks[-1]
    bare = strip_sql_fences(text)
    return bare if _SQL_START_RE.match(bare) else None


def _usage_tokens(usage: dict | None) -> tuple[int, int]:
    """Open WebUI stores input_/output_tokens summed over all tool rounds and
    prompt_/completion_tokens for the last round only; prefer the totals."""
    usage = usage or {}
    tin = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    tout = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return int(tin), int(tout)


def summarise_output(output: list[dict] | None) -> tuple[list[dict], int]:
    """Pair function_call / function_call_output items into a compact transcript."""
    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    for item in output or []:
        kind = item.get("type")
        if kind == "function_call":
            rec = {
                "call_id": item.get("call_id"),
                "name": item.get("name"),
                "arguments": item.get("arguments"),
                "output": None,
            }
            calls.append(rec)
            if rec["call_id"]:
                by_id[rec["call_id"]] = rec
        elif kind == "function_call_output":
            target = by_id.get(item.get("call_id"))
            out = item.get("output")
            if isinstance(out, list):  # [{type: input_text, text: ...}]
                out = "".join(p.get("text", "") for p in out if isinstance(p, dict))
            if target is not None:
                target["output"] = out
            else:
                calls.append({"call_id": item.get("call_id"), "name": None,
                              "arguments": None, "output": out})
    return calls, sum(1 for c in calls if c["name"])


def parse_assistant_message(chat: dict, message_id: str) -> dict:
    """Locate the assistant message in GET /api/v1/chats/{id} output."""
    messages = ((chat.get("chat") or {}).get("history") or {}).get("messages") or {}
    msg = messages.get(message_id)
    if msg is None:
        raise KeyError(f"assistant message {message_id} not found in chat")
    return msg


class OpenWebUIClient:
    def __init__(self, base_url: str, api_key: str, *, timeout_s: float = 300.0) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_s, connect=15.0),
        )

    def close(self) -> None:
        self._http.close()

    def version(self) -> str | None:
        try:
            return self._http.get("/api/version").json().get("version")
        except Exception:
            return None

    def create_chat(self, *, model: str, title: str) -> str:
        r = self._http.post(
            "/api/v1/chats/new",
            json={"chat": {"title": title, "models": [model], "history": {"messages": {}}}},
        )
        r.raise_for_status()
        return r.json()["id"]

    def complete(
        self, *, chat_id: str, message_id: str, model: str, system: str, user: str,
        tool_ids: list[str],
    ) -> None:
        payload = {
            "model": model,
            "chat_id": chat_id,
            "id": message_id,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tool_ids": tool_ids,
            "features": {
                "web_search": False, "code_interpreter": False, "image_generation": False,
                "memory": False, "voice": False,
            },
            "params": {"function_calling": "native"},
            "background_tasks": {"title_generation": False, "tags_generation": False,
                                 "follow_up_generation": False},
        }
        with self._http.stream("POST", "/api/chat/completions", json=payload) as r:
            if r.status_code >= 400:
                r.read()
                raise httpx.HTTPStatusError(
                    f"{r.status_code}: {r.text[:500]}", request=r.request, response=r
                )
            for _ in r.iter_bytes():  # drain; the result is persisted to the chat
                pass

    def get_chat(self, chat_id: str) -> dict:
        r = self._http.get(f"/api/v1/chats/{chat_id}")
        r.raise_for_status()
        return r.json()

    def delete_chat(self, chat_id: str) -> None:
        with contextlib.suppress(Exception):
            self._http.delete(f"/api/v1/chats/{chat_id}")


_INFRA_MSG_RE = re.compile(
    r"Server Connection Error|Cannot connect|ClientConnectorError|ServerDisconnected|"
    r"Connection (reset|refused|closed)|\b50[234]\b", re.I)
RETRY_BACKOFF_S = (15, 45, 90)


def is_infra_error(exc: Exception | None = None, message: str | None = None) -> bool:
    """Transport/provider outages (not the model's fault): retry, never score."""
    if exc is not None:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500
        return isinstance(exc, httpx.TransportError) and not isinstance(exc, httpx.ReadTimeout)
    return bool(message and _INFRA_MSG_RE.search(message))


def _attempt(client, *, model, tool_ids, system_prompt, user_prompt, title, keep_chat):
    start = time.perf_counter()
    chat_id: str | None = None
    try:
        chat_id = client.create_chat(model=model, title=title)
        message_id = str(uuid.uuid4())
        client.complete(chat_id=chat_id, message_id=message_id, model=model,
                        system=system_prompt, user=user_prompt, tool_ids=tool_ids)
        latency = int((time.perf_counter() - start) * 1000)
        msg = parse_assistant_message(client.get_chat(chat_id), message_id)
    except Exception as exc:
        return OpenWebUIRunResult(
            sql=None, final_content="", latency_ms_gen=int((time.perf_counter() - start) * 1000),
            tokens_in=0, tokens_out=0, error=f"{type(exc).__name__}: {exc}"[:1000],
            infra_error=is_infra_error(exc),
        )
    finally:
        if chat_id and not keep_chat:
            client.delete_chat(chat_id)

    content = msg.get("content") or ""
    tool_calls, rounds = summarise_output(msg.get("output"))
    tin, tout = _usage_tokens(msg.get("usage"))
    err, infra = None, False
    if msg.get("error"):
        err = f"openwebui message error: {msg['error']}"[:1000]
        infra = is_infra_error(message=str(msg["error"]))
    sql = extract_final_sql(content)
    if sql is None and err is None:
        err = "no final SQL in answer"
    return OpenWebUIRunResult(
        sql=sql, final_content=content, latency_ms_gen=latency, tokens_in=tin,
        tokens_out=tout, tool_calls=tool_calls, rounds=rounds, error=err, infra_error=infra,
    )


def run_openwebui_agent(
    client: OpenWebUIClient,
    *,
    model: str,
    tool_ids: list[str],
    system_prompt: str,
    user_prompt: str,
    title: str,
    keep_chat: bool = False,
    backoff_s: tuple[int, ...] = RETRY_BACKOFF_S,
) -> OpenWebUIRunResult:
    """One question; infra errors are retried with a fresh chat after each backoff."""
    res = None
    for attempt, wait in enumerate((0, *backoff_s)):
        if wait:
            time.sleep(wait)
        res = _attempt(client, model=model, tool_ids=tool_ids, system_prompt=system_prompt,
                       user_prompt=user_prompt, title=title, keep_chat=keep_chat)
        res.attempts = attempt + 1
        if not res.infra_error:
            break
    return res
