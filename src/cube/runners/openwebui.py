"""Open WebUI agentic runner (optional backend via Open WebUI native tool loop)."""
from __future__ import annotations

import contextlib
import re
import time
import uuid

import httpx

from cube.engines.base import DatabaseEngine
from cube.prompts.builder import build_prompts
from cube.prompts.sql_extract import extract_sql, strip_sql_fences
from cube.types import RunnerRequest, RunnerResult

_DETAILS_RE = re.compile(r"<details\b[^>]*>.*?</details>", re.S | re.I)
_SQL_BLOCK_RE = re.compile(r"```(?:sql|postgresql|postgres)?[ \t]*\n(.*?)```", re.S | re.I)
_SQL_START_RE = re.compile(r"^\s*(with|select)\b", re.I)
_INFRA_MSG_RE = re.compile(
    r"Server Connection Error|Cannot connect|ClientConnectorError|ServerDisconnected|"
    r"Connection (reset|refused|closed)|\b50[234]\b",
    re.I,
)
RETRY_BACKOFF_S = (15, 45, 90)


def visible_content(content: str) -> str:
    return _DETAILS_RE.sub("", content or "").strip()


def extract_final_sql(content: str) -> str | None:
    text = visible_content(content)
    blocks = [b.strip() for b in _SQL_BLOCK_RE.findall(text) if b.strip()]
    if blocks:
        return blocks[-1]
    bare = strip_sql_fences(text)
    if _SQL_START_RE.match(bare):
        return bare
    return extract_sql(content)


def _usage_tokens(usage: dict | None) -> tuple[int, int]:
    usage = usage or {}
    tin = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    tout = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return int(tin), int(tout)


def summarise_output(output: list[dict] | None) -> tuple[list[dict], int]:
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
            if isinstance(out, list):
                out = "".join(p.get("text", "") for p in out if isinstance(p, dict))
            if target is not None:
                target["output"] = out
            else:
                calls.append(
                    {
                        "call_id": item.get("call_id"),
                        "name": None,
                        "arguments": None,
                        "output": out,
                    }
                )
    return calls, sum(1 for c in calls if c["name"])


def parse_assistant_message(chat: dict, message_id: str) -> dict:
    messages = ((chat.get("chat") or {}).get("history") or {}).get("messages") or {}
    msg = messages.get(message_id)
    if msg is None:
        raise KeyError(f"assistant message {message_id} not found in chat")
    return msg


def is_infra_error(exc: Exception | None = None, message: str | None = None) -> bool:
    if exc is not None:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500
        return isinstance(exc, httpx.TransportError) and not isinstance(exc, httpx.ReadTimeout)
    return bool(message and _INFRA_MSG_RE.search(message))


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
        self,
        *,
        chat_id: str,
        message_id: str,
        model: str,
        system: str,
        user: str,
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
                "web_search": False,
                "code_interpreter": False,
                "image_generation": False,
                "memory": False,
                "voice": False,
            },
            "params": {"function_calling": "native"},
            "background_tasks": {
                "title_generation": False,
                "tags_generation": False,
                "follow_up_generation": False,
            },
        }
        with self._http.stream("POST", "/api/chat/completions", json=payload) as r:
            if r.status_code >= 400:
                r.read()
                raise httpx.HTTPStatusError(
                    f"{r.status_code}: {r.text[:500]}", request=r.request, response=r
                )
            for _ in r.iter_bytes():
                pass

    def get_chat(self, chat_id: str) -> dict:
        r = self._http.get(f"/api/v1/chats/{chat_id}")
        r.raise_for_status()
        return r.json()

    def delete_chat(self, chat_id: str) -> None:
        with contextlib.suppress(Exception):
            self._http.delete(f"/api/v1/chats/{chat_id}")


def _attempt(
    client: OpenWebUIClient,
    *,
    model: str,
    tool_ids: list[str],
    system_prompt: str,
    user_prompt: str,
    title: str,
    keep_chat: bool,
) -> RunnerResult:
    start = time.perf_counter()
    chat_id: str | None = None
    try:
        chat_id = client.create_chat(model=model, title=title)
        message_id = str(uuid.uuid4())
        client.complete(
            chat_id=chat_id,
            message_id=message_id,
            model=model,
            system=system_prompt,
            user=user_prompt,
            tool_ids=tool_ids,
        )
        latency = int((time.perf_counter() - start) * 1000)
        msg = parse_assistant_message(client.get_chat(chat_id), message_id)
    except Exception as exc:
        return RunnerResult(
            sql=None,
            raw_response="",
            latency_ms_gen=int((time.perf_counter() - start) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:1000],
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
    return RunnerResult(
        sql=sql,
        raw_response=content,
        latency_ms_gen=latency,
        tokens_in=tin,
        tokens_out=tout,
        tool_calls=tool_calls,
        rounds=rounds,
        error=err,
        infra_error=infra,
    )


class OpenWebUIRunner:
    name = "openwebui"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        tool_id: str = "run_sql",
        timeout_s: float = 300.0,
        keep_chats: bool = False,
        backoff_s: tuple[int, ...] = RETRY_BACKOFF_S,
        client: OpenWebUIClient | None = None,
    ) -> None:
        self.model = model
        self.tool_ids = [tool_id] if tool_id else []
        self.keep_chats = keep_chats
        self.backoff_s = backoff_s
        self._client = client or OpenWebUIClient(base_url, api_key, timeout_s=timeout_s)

    def run(
        self,
        req: RunnerRequest,
        *,
        engine: DatabaseEngine | None = None,
    ) -> RunnerResult:
        system_prompt, user_prompt = build_prompts(
            dialect=req.dialect,
            mode=req.mode,
            schema_text=req.schema_text,
            question_text=req.question.question,
            semantic_md=req.semantic_md,
            agentic=True,
        )
        title = f"cube q{req.question.id:02d} ({req.mode})"
        res: RunnerResult | None = None
        for attempt, wait in enumerate((0, *self.backoff_s)):
            if wait:
                time.sleep(wait)
            res = _attempt(
                self._client,
                model=self.model,
                tool_ids=self.tool_ids,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                title=title,
                keep_chat=self.keep_chats,
            )
            res.attempts = attempt + 1
            if not res.infra_error:
                break
        assert res is not None
        return res
