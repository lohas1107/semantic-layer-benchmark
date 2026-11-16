from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import httpx

from cube.prompts.builder import build_prompts
from cube.runners import openwebui as ow
from cube.runners.openwebui import (
    OpenWebUIRunner,
    extract_final_sql,
    parse_assistant_message,
    summarise_output,
)
from cube.storage import RunStore
from cube.types import BenchmarkQuestion, RunnerRequest

REPO = Path(__file__).resolve().parents[1]


def test_extract_final_sql_takes_last_block_outside_details():
    content = (
        '<details type="reasoning"><summary>Thought</summary>```sql\nSELECT 0\n```</details>\n'
        "First try:\n```sql\nSELECT 1\n```\n"
        "Final:\n```sql\nSELECT count(*) FROM retail.DimProduct\n```"
    )
    assert extract_final_sql(content) == "SELECT count(*) FROM retail.DimProduct"


def test_extract_final_sql_bare_select():
    assert extract_final_sql("SELECT 1") == "SELECT 1"
    assert extract_final_sql("I could not find the table.") is None


def test_summarise_output_pairs_calls_and_counts_rounds():
    output = [
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "run_sql",
            "arguments": '{"sql": "SELECT 1"}',
        },
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": [{"type": "input_text", "text": "[[1]]"}],
        },
        {
            "type": "function_call",
            "call_id": "c2",
            "name": "run_sql",
            "arguments": '{"sql": "SELECT 2"}',
        },
        {"type": "function_call_output", "call_id": "c2", "output": "[[2]]"},
    ]
    calls, rounds = summarise_output(output)
    assert rounds == 2
    assert calls[0]["output"] == "[[1]]"
    assert calls[1]["output"] == "[[2]]"


def test_parse_assistant_message():
    chat = {"chat": {"history": {"messages": {"m1": {"content": "ok"}}}}}
    assert parse_assistant_message(chat, "m1") == {"content": "ok"}


def test_build_prompts_agentic_postgres():
    sys_raw, _ = build_prompts(
        dialect="PostgreSQL", mode="raw", schema_text="DDL", question_text="Q?", agentic=True
    )
    sys_sem, user_sem = build_prompts(
        dialect="PostgreSQL",
        mode="semantic",
        schema_text="DDL",
        question_text="Q?",
        semantic_md="SEM",
        agentic=True,
    )
    assert "run_sql" in sys_raw and "PostgreSQL" in sys_raw
    assert "SEMANTIC LAYER" in sys_sem and user_sem.startswith("# SEMANTIC LAYER")


def test_discover_scored_systems(tmp_path):
    store = RunStore("r1", runs_root=tmp_path)
    (store.scoring_dir("owui_raw")).mkdir(parents=True)
    (store.scoring_dir("empty_dir")).mkdir(parents=True)
    (store.scoring_dir("owui_raw") / "q01.json").write_text("{}")
    assert store.discover_scored_systems() == ["owui_raw"]


def test_outer_has_order_by_and_compare():
    sys.path.insert(0, str(REPO / "infra" / "contoso-pg"))
    from verify_pg_dataset import compare, outer_has_order_by

    assert outer_has_order_by("SELECT a FROM t ORDER BY a")
    assert not outer_has_order_by("SELECT * FROM (SELECT a FROM t ORDER BY a LIMIT 3) s")
    assert not outer_has_order_by("SELECT row_number() OVER (ORDER BY a) FROM t")

    ref = {"rows": [["a", 1.0], ["b", 1.0]], "error": None}
    assert compare(ref, [["b", 1.0], ["a", 1.0]], ordered=False)[0] == "match"
    assert compare(ref, [["c", 1.0], ["d", 1.0]], ordered=True, ties=[1])[0] == "match_ties"


def test_clickhouse_csv_emulation():
    sys.path.insert(0, str(REPO / "infra" / "contoso-pg"))
    from load_kaggle import clickhouse_csv_records

    src = 'h1,h2,h3\r\n a , "b " ,\r\n"x,""y""",  ,""\r\n"multi\nline",2,3\r\n'
    out = list(clickhouse_csv_records(io.StringIO(src, newline="")))
    assert out == ['h1,h2,h3\n', '"a","b ",\n', '"x,""y""",,""\n', '"multi\nline","2","3"\n']


def test_pg_benchmark_json_is_complete():
    data = json.loads((REPO / "datasets" / "postgres" / "benchmark.json").read_text())
    ids = [q["id"] for q in data["questions"]]
    assert ids == list(range(1, 101))
    assert all(q["expected_sql"] and q["expected_sql_clickhouse"] for q in data["questions"])


def test_infra_error_classification_and_retry():
    req = httpx.Request("POST", "http://x/api/chat/completions")
    err500 = httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))
    err400 = httpx.HTTPStatusError("400", request=req, response=httpx.Response(400, request=req))
    assert ow.is_infra_error(err500)
    assert not ow.is_infra_error(err400)
    assert ow.is_infra_error(httpx.ConnectError("boom"))
    assert not ow.is_infra_error(httpx.ReadTimeout("slow model"))

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
            return {
                "chat": {
                    "history": {
                        "messages": {
                            "m": {"content": "```sql\nSELECT 1\n```", "usage": {}}
                        }
                    }
                }
            }

        def delete_chat(self, _cid):
            pass

    orig_uuid = ow.uuid.uuid4
    ow.uuid.uuid4 = lambda: "m"
    try:
        runner = OpenWebUIRunner(
            model="x",
            base_url="http://x",
            api_key="k",
            backoff_s=(0, 0, 0),
            client=FlakyClient(),  # type: ignore[arg-type]
        )
        q = BenchmarkQuestion(id=1, level="simple", question="Q?", expected_sql="SELECT 1")
        req = RunnerRequest(question=q, schema_text="DDL", dialect="PostgreSQL", mode="raw")
        res = runner.run(req)
    finally:
        ow.uuid.uuid4 = orig_uuid
    assert res.sql == "SELECT 1" and res.attempts == 3 and not res.infra_error
