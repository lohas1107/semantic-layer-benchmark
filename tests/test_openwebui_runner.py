from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from benchmarks.text_to_sql.runners.openwebui import (
    OPENWEBUI_RAW_SYSTEM_PROMPT,
    OPENWEBUI_SEMANTIC_SYSTEM_PROMPT,
    build_prompts,
    extract_final_sql,
    parse_assistant_message,
    summarise_output,
)
from scripts.score_run import discover_systems
from docker.contoso.verify_pg_dataset import compare, outer_has_order_by

REPO = Path(__file__).resolve().parents[1]


def test_extract_final_sql_takes_last_block_outside_details():
    content = (
        '<details type="reasoning"><summary>Thought</summary>```sql\nSELECT 0\n```</details>\n'
        "First try:\n```sql\nSELECT 1\n```\n"
        "Final:\n```sql\nSELECT count(*) FROM retail.DimProduct\n```"
    )
    assert extract_final_sql(content) == "SELECT count(*) FROM retail.DimProduct"


def test_extract_final_sql_bare_and_missing():
    assert extract_final_sql("SELECT 1") == "SELECT 1"
    assert extract_final_sql("WITH a AS (SELECT 1) SELECT * FROM a") is not None
    assert extract_final_sql("I could not answer.") is None


def test_summarise_output_pairs_calls_and_results():
    output = [
        {"type": "message", "content": []},
        {"type": "function_call", "call_id": "c1", "name": "run_sql",
         "arguments": '{"query": "SELECT 1"}'},
        {"type": "function_call_output", "call_id": "c1",
         "output": [{"type": "input_text", "text": '{"rows": [[1]]}'}]},
        {"type": "function_call", "call_id": "c2", "name": "run_sql", "arguments": "{}"},
    ]
    calls, rounds = summarise_output(output)
    assert rounds == 2
    assert calls[0]["output"] == '{"rows": [[1]]}'
    assert calls[1]["output"] is None


def test_usage_prefers_totals_over_last_round():
    from benchmarks.text_to_sql.runners.openwebui import _usage_tokens

    usage = {"input_tokens": 318, "output_tokens": 43, "prompt_tokens": 187,
             "completion_tokens": 20, "incomplete": False}
    assert _usage_tokens(usage) == (318, 43)
    assert _usage_tokens({"prompt_tokens": 5, "completion_tokens": 2}) == (5, 2)
    assert _usage_tokens({"incomplete": True}) == (0, 0)


def test_parse_assistant_message():
    chat = {"chat": {"history": {"messages": {"m1": {"content": "x", "usage": {}}}}}}
    assert parse_assistant_message(chat, "m1")["content"] == "x"


def test_build_prompts_variants():
    sys_raw, user_raw = build_prompts(schema_text="DDL", question_text="Q?", semantic_md=None)
    assert sys_raw == OPENWEBUI_RAW_SYSTEM_PROMPT and user_raw.endswith("Question: Q?")
    sys_sem, user_sem = build_prompts(schema_text="DDL", question_text="Q?", semantic_md="SEM")
    assert sys_sem == OPENWEBUI_SEMANTIC_SYSTEM_PROMPT and user_sem.startswith("# SEMANTIC LAYER")
    assert "run_sql" in sys_raw and "PostgreSQL" in sys_raw


def test_discover_systems(tmp_path):
    run = tmp_path / "runs" / "r1"
    for d in ("reference", "scoring", "openwebui_x_raw", "empty_dir"):
        (run / d).mkdir(parents=True)
    (run / "reference" / "q01.json").write_text("{}")
    (run / "openwebui_x_raw" / "q01.json").write_text("{}")
    assert discover_systems("r1", root=tmp_path) == ["openwebui_x_raw"]


def test_outer_has_order_by():
    assert outer_has_order_by("SELECT a FROM t ORDER BY a")
    assert not outer_has_order_by("SELECT * FROM (SELECT a FROM t ORDER BY a LIMIT 3) s")
    assert not outer_has_order_by("SELECT row_number() OVER (ORDER BY a) FROM t")


def test_compare_ties_and_tolerance():
    ref = {"rows": [["a", 1.0], ["b", 1.0]], "error": None}
    assert compare(ref, [["b", 1.0], ["a", 1.0]], ordered=False)[0] == "match"
    assert compare(ref, [["c", 1.0], ["d", 1.0]], ordered=True, ties=[1])[0] == "match_ties"
    assert compare({"rows": [[100.01]], "error": None}, [[100.0]], ordered=False)[0] == "match"
    assert compare({"rows": [[1e9]], "error": None}, [[1e9 + 0.5]], ordered=False)[0] == "match"
    assert compare({"rows": [[100.5]], "error": None}, [[100.0]], ordered=False)[0] == "mismatch"


def test_clickhouse_csv_emulation():
    sys.path.insert(0, str(REPO / "docker" / "contoso"))
    from load_kaggle import clickhouse_csv_records

    src = 'h1,h2,h3\r\n a , "b " ,\r\n"x,""y""",  ,""\r\n"multi\nline",2,3\r\n'
    out = list(clickhouse_csv_records(io.StringIO(src, newline="")))
    assert out == ['h1,h2,h3\n', '"a","b ",\n', '"x,""y""",,""\n', '"multi\nline","2","3"\n']


def test_pg_benchmark_json_is_complete():
    data = json.loads((REPO / "text_to_sql_benchmark.pg.json").read_text())
    ids = [q["id"] for q in data["questions"]]
    assert ids == list(range(1, 101))
    assert all(q["expected_sql"] and q["expected_sql_clickhouse"] for q in data["questions"])
    assert not any("toYear(" in q["expected_sql"] or "count()" in q["expected_sql"]
                   for q in data["questions"])


def test_infra_error_classification_and_retry():
    import httpx

    from benchmarks.text_to_sql.runners import openwebui as ow

    req = httpx.Request("POST", "http://x/api/chat/completions")
    err500 = httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))
    err400 = httpx.HTTPStatusError("400", request=req, response=httpx.Response(400, request=req))
    assert ow.is_infra_error(err500)
    assert not ow.is_infra_error(err400)
    assert ow.is_infra_error(httpx.ConnectError("boom"))
    assert not ow.is_infra_error(httpx.ReadTimeout("slow model"))
    assert ow.is_infra_error(message="Open WebUI: Server Connection Error")
    assert not ow.is_infra_error(message="relation does not exist")

    class FlakyClient:
        def __init__(self):
            self.calls = 0

        def create_chat(self, **_):
            return "c"

        def complete(self, **_):
            self.calls += 1
            if self.calls < 3:
                raise err500

        def get_chat(self, _cid):
            return {"chat": {"history": {"messages": {
                ow_mid[0]: {"content": "```sql\nSELECT 1\n```", "usage": {}}}}}}

        def delete_chat(self, _cid):
            pass

    ow_mid = ["m"]
    orig_uuid = ow.uuid.uuid4
    ow.uuid.uuid4 = lambda: "m"
    try:
        client = FlakyClient()
        res = ow.run_openwebui_agent(client, model="x", tool_ids=[], system_prompt="s",
                                     user_prompt="u", title="t", backoff_s=(0, 0, 0))
    finally:
        ow.uuid.uuid4 = orig_uuid
    assert res.sql == "SELECT 1" and res.attempts == 3 and not res.infra_error
