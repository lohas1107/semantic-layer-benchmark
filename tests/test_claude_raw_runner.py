from __future__ import annotations

import json

from benchmarks.text_to_sql.runners.claude_raw import (
    ClaudeRawParseResult,
    build_user_prompt,
    parse_claude_cli_output,
    strip_sql_fences,
)


def test_strip_sql_fences_removes_triple_backtick_sql():
    s = "```sql\nSELECT 1\n```"
    assert strip_sql_fences(s) == "SELECT 1"


def test_strip_sql_fences_removes_plain_triple_backticks():
    s = "```\nSELECT 2\n```"
    assert strip_sql_fences(s) == "SELECT 2"


def test_strip_sql_fences_passes_through_unfenced():
    s = "SELECT 3"
    assert strip_sql_fences(s) == "SELECT 3"


def test_strip_sql_fences_trims_whitespace():
    s = "  \n  SELECT 4  \n  "
    assert strip_sql_fences(s) == "SELECT 4"


def test_parse_claude_cli_output_extracts_sql_and_tokens(fixtures_dir):
    stdout = (fixtures_dir / "claude_raw_response.json").read_bytes()
    result = parse_claude_cli_output(stdout)
    assert isinstance(result, ClaudeRawParseResult)
    assert result.sql == "SELECT count() FROM retail.DimProduct"
    assert result.tokens_in == 8100
    assert result.tokens_out == 25
    assert result.is_error is False


def test_parse_claude_cli_output_handles_error_flag():
    stdout = json.dumps({
        "result": "",
        "usage": {"input_tokens": 10, "output_tokens": 0},
        "is_error": True,
    }).encode()
    result = parse_claude_cli_output(stdout)
    assert result.is_error is True
    assert result.sql == ""


def test_build_user_prompt_includes_schema_and_question():
    schema = "-- DDL here\nCREATE TABLE retail.DimProduct (ProductKey UInt32);"
    prompt = build_user_prompt(schema, "How many products?")
    assert "CREATE TABLE retail.DimProduct" in prompt
    assert "Question: How many products?" in prompt
