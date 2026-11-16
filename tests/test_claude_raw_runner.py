from __future__ import annotations

import json

from cube.prompts.builder import build_prompts
from cube.prompts.sql_extract import strip_sql_fences
from cube.runners.claude_cli import ClaudeCLIParseResult, parse_claude_cli_output


def test_strip_sql_fences_removes_triple_backtick_sql():
    assert strip_sql_fences("```sql\nSELECT 1\n```") == "SELECT 1"


def test_strip_sql_fences_removes_plain_triple_backticks():
    assert strip_sql_fences("```\nSELECT 2\n```") == "SELECT 2"


def test_strip_sql_fences_passes_through_unfenced():
    assert strip_sql_fences("SELECT 3") == "SELECT 3"


def test_strip_sql_fences_trims_whitespace():
    assert strip_sql_fences("  \n  SELECT 4  \n  ") == "SELECT 4"


def test_parse_claude_cli_output_extracts_sql_and_tokens(fixtures_dir):
    stdout = (fixtures_dir / "claude_raw_response.json").read_bytes()
    result = parse_claude_cli_output(stdout)
    assert isinstance(result, ClaudeCLIParseResult)
    assert result.sql == "SELECT count() FROM retail.DimProduct"
    assert result.tokens_in == 8100
    assert result.tokens_out == 25
    assert result.is_error is False


def test_parse_claude_cli_output_handles_error_flag():
    stdout = json.dumps(
        {
            "result": "",
            "usage": {"input_tokens": 10, "output_tokens": 0},
            "is_error": True,
        }
    ).encode()
    result = parse_claude_cli_output(stdout)
    assert result.is_error is True
    assert result.sql == ""


def test_build_prompts_includes_schema_and_question():
    schema = "-- DDL here\nCREATE TABLE retail.DimProduct (ProductKey UInt32);"
    sys_p, user_p = build_prompts(
        dialect="ClickHouse",
        mode="raw",
        schema_text=schema,
        question_text="How many products?",
    )
    assert "ClickHouse" in sys_p
    assert "CREATE TABLE retail.DimProduct" in user_p
    assert "Question: How many products?" in user_p
