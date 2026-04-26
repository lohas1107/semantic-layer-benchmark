from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from benchmarks.text_to_sql.scorer import (
    build_judge_user_prompt,
    parse_judge_output,
    score_one,
)


def test_build_judge_user_prompt_contains_all_sections():
    prompt = build_judge_user_prompt(
        question="How many products?",
        schema_text="CREATE TABLE retail.DimProduct (ProductKey UInt32);",
        expected_sql="SELECT count() FROM retail.DimProduct",
        expected_rows=[[2517]],
        expected_row_count=1,
        candidate_sql="SELECT COUNT(*) FROM retail.DimProduct",
        candidate_rows=[[2517]],
        candidate_row_count=1,
    )
    assert "How many products?" in prompt
    assert "CREATE TABLE retail.DimProduct" in prompt
    assert "Reference SQL:" in prompt
    assert "SELECT count() FROM retail.DimProduct" in prompt
    assert "Candidate SQL:" in prompt
    assert "SELECT COUNT(*) FROM retail.DimProduct" in prompt
    assert "[[2517]]" in prompt


def test_build_judge_user_prompt_shows_first_10_rows():
    expected_rows = [[i] for i in range(20)]
    prompt = build_judge_user_prompt(
        question="q",
        schema_text="s",
        expected_sql="ref",
        expected_rows=expected_rows,
        expected_row_count=20,
        candidate_sql="cand",
        candidate_rows=expected_rows,
        candidate_row_count=20,
    )
    assert "first 10 of 20" in prompt
    assert "[[0], [1], [2], [3], [4], [5], [6], [7], [8], [9]]" in prompt


def test_parse_judge_output_pass(fixtures_dir):
    stdout = (fixtures_dir / "judge_response_pass.json").read_bytes()
    outcome = parse_judge_output(stdout)
    assert outcome.verdict == "pass"
    assert "match" in outcome.reason.lower()
    assert outcome.tokens_in == 4200
    assert outcome.tokens_out == 32


def test_parse_judge_output_reads_structured_output(fixtures_dir):
    """When the CLI uses --json-schema, the parsed object is in
    `structured_output`, not in `result` (which is empty). Must prefer
    structured_output when present."""
    stdout = (fixtures_dir / "judge_response_structured.json").read_bytes()
    outcome = parse_judge_output(stdout)
    assert outcome.verdict == "pass"
    assert outcome.reason == "Counts match exactly."
    assert outcome.tokens_in == 4200
    assert outcome.tokens_out == 32


def test_parse_judge_output_fail(fixtures_dir):
    stdout = (fixtures_dir / "judge_response_fail.json").read_bytes()
    outcome = parse_judge_output(stdout)
    assert outcome.verdict == "fail"


def test_parse_judge_output_malformed_raises():
    with pytest.raises(ValueError):
        parse_judge_output(b"not json")


def test_parse_judge_output_invalid_verdict_raises():
    bad = json.dumps({
        "result": '{"verdict": "maybe", "reason": "meh"}',
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }).encode()
    with pytest.raises(ValueError):
        parse_judge_output(bad)


def test_score_one_skips_judge_when_candidate_has_error():
    candidate = {
        "sql": None,
        "rows": None,
        "row_count": None,
        "claude_error": "claude exit 1",
        "sql_error": None,
        "error": None,
    }
    reference = {"rows": [[1]], "row_count": 1, "sql": "SELECT 1", "error": None}

    with patch("benchmarks.text_to_sql.scorer._invoke_judge") as invoke_mock:
        out = score_one(
            question="q",
            schema_text="s",
            reference=reference,
            candidate=candidate,
            candidate_sql_key="sql",
        )
    assert out.verdict == "fail"
    assert out.skipped_judge is True
    assert "claude exit 1" in out.reason
    invoke_mock.assert_not_called()


def test_score_one_trivial_both_empty_passes_without_judge():
    candidate = {
        "sql": "SELECT * FROM t WHERE 1=0",
        "rows": [],
        "row_count": 0,
        "claude_error": None,
        "sql_error": None,
    }
    reference = {
        "rows": [],
        "row_count": 0,
        "sql": "SELECT * FROM t WHERE 1=0",
        "error": None,
    }
    with patch("benchmarks.text_to_sql.scorer._invoke_judge") as invoke_mock:
        out = score_one(
            question="q",
            schema_text="s",
            reference=reference,
            candidate=candidate,
            candidate_sql_key="sql",
        )
    assert out.verdict == "pass"
    assert out.skipped_judge is True
    assert out.reason == "trivial_empty_match"
    invoke_mock.assert_not_called()


def test_score_one_reference_error_marks_excluded():
    """When the reference query errored, we cannot grade; mark as excluded
    (so the reporter can exclude from the accuracy denominator) rather than
    fail both systems."""
    candidate = {
        "sql": "SELECT 1",
        "rows": [[1]],
        "row_count": 1,
        "claude_error": None,
        "sql_error": None,
    }
    reference = {
        "rows": None,
        "row_count": None,
        "sql": "SELECT complex_query()",
        "error": "Code: 241. Memory limit exceeded.",
    }
    with patch("benchmarks.text_to_sql.scorer._invoke_judge") as invoke_mock:
        out = score_one(
            question="q",
            schema_text="s",
            reference=reference,
            candidate=candidate,
            candidate_sql_key="sql",
        )
    assert out.verdict == "excluded"
    assert out.skipped_judge is True
    assert "reference_error" in out.reason
    invoke_mock.assert_not_called()


def test_score_one_calls_judge_for_normal_case(fixtures_dir):
    candidate = {
        "sql": "SELECT COUNT(*) FROM retail.DimProduct",
        "rows": [[2517]],
        "row_count": 1,
        "claude_error": None,
        "sql_error": None,
    }
    reference = {
        "rows": [[2517]],
        "row_count": 1,
        "sql": "SELECT count() FROM retail.DimProduct",
        "error": None,
    }
    fake_stdout = (fixtures_dir / "judge_response_pass.json").read_bytes()
    with patch(
        "benchmarks.text_to_sql.scorer._invoke_judge",
        return_value=(fake_stdout, 2100),
    ) as invoke_mock:
        out = score_one(
            question="q",
            schema_text="s",
            reference=reference,
            candidate=candidate,
            candidate_sql_key="sql",
        )
    assert out.verdict == "pass"
    assert out.skipped_judge is False
    assert out.judge_tokens_in == 4200
    assert out.judge_tokens_out == 32
    invoke_mock.assert_called_once()
