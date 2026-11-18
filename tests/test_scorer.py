from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cube.scoring.llm_judge import (
    LLMJudge,
    build_judge_user_prompt,
    parse_judge_cli_output,
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
    assert "Candidate SQL:" in prompt
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


def test_parse_judge_cli_output_pass(fixtures_dir):
    stdout = (fixtures_dir / "judge_response_pass.json").read_bytes()
    outcome = parse_judge_cli_output(stdout)
    assert outcome.verdict == "pass"
    assert "match" in outcome.reason.lower()
    assert outcome.tokens_in == 4200
    assert outcome.tokens_out == 32


def test_parse_judge_cli_output_reads_structured_output(fixtures_dir):
    stdout = (fixtures_dir / "judge_response_structured.json").read_bytes()
    outcome = parse_judge_cli_output(stdout)
    assert outcome.verdict == "pass"
    assert outcome.reason == "Counts match exactly."


def test_parse_judge_cli_output_fail(fixtures_dir):
    stdout = (fixtures_dir / "judge_response_fail.json").read_bytes()
    outcome = parse_judge_cli_output(stdout)
    assert outcome.verdict == "fail"


def test_parse_judge_cli_output_malformed_raises():
    with pytest.raises(ValueError):
        parse_judge_cli_output(b"not json")


def test_parse_judge_cli_output_invalid_verdict_raises():
    bad = json.dumps(
        {
            "result": '{"verdict": "maybe", "reason": "meh"}',
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    ).encode()
    with pytest.raises(ValueError):
        parse_judge_cli_output(bad)


def test_score_sync_skips_judge_when_candidate_has_error():
    judge = LLMJudge(runner="claude_cli", model="claude-sonnet-4-6")
    with patch.object(judge, "_invoke_claude_cli") as invoke_mock:
        out = judge.score_sync(
            question="q",
            schema_text="s",
            expected_sql="SELECT 1",
            expected_rows=[[1]],
            expected_row_count=1,
            candidate_sql=None,
            candidate_rows=[],
            candidate_row_count=0,
            candidate_error="claude exit 1",
            reference_error=None,
        )
    assert out.verdict == "fail"
    assert out.skipped_judge is True
    assert "claude exit 1" in out.reason
    invoke_mock.assert_not_called()


def test_score_sync_trivial_both_empty_passes_without_judge():
    judge = LLMJudge(runner="claude_cli", model="claude-sonnet-4-6")
    with patch.object(judge, "_invoke_claude_cli") as invoke_mock:
        out = judge.score_sync(
            question="q",
            schema_text="s",
            expected_sql="SELECT * FROM t WHERE 1=0",
            expected_rows=[],
            expected_row_count=0,
            candidate_sql="SELECT * FROM t WHERE 1=0",
            candidate_rows=[],
            candidate_row_count=0,
            candidate_error=None,
            reference_error=None,
        )
    assert out.verdict == "pass"
    assert out.skipped_judge is True
    invoke_mock.assert_not_called()


def test_score_sync_reference_error_marks_excluded():
    judge = LLMJudge(runner="claude_cli", model="claude-sonnet-4-6")
    with patch.object(judge, "_invoke_claude_cli") as invoke_mock:
        out = judge.score_sync(
            question="q",
            schema_text="s",
            expected_sql="SELECT complex_query()",
            expected_rows=[],
            expected_row_count=0,
            candidate_sql="SELECT 1",
            candidate_rows=[[1]],
            candidate_row_count=1,
            candidate_error=None,
            reference_error="Memory limit exceeded",
        )
    assert out.verdict == "excluded"
    assert out.analytical_verdict == "excluded"
    assert out.skipped_judge is True
    invoke_mock.assert_not_called()


def test_score_sync_calls_judge_for_normal_case(fixtures_dir):
    judge = LLMJudge(runner="claude_cli", model="claude-sonnet-4-6")
    fake_stdout = (fixtures_dir / "judge_response_pass.json").read_bytes()
    outcome = parse_judge_cli_output(fake_stdout)
    with patch.object(judge, "_invoke_claude_cli", return_value=outcome) as invoke_mock:
        out = judge.score_sync(
            question="q",
            schema_text="s",
            expected_sql="SELECT count() FROM retail.DimProduct",
            expected_rows=[[2517]],
            expected_row_count=1,
            candidate_sql="SELECT COUNT(*) FROM retail.DimProduct",
            candidate_rows=[[2517]],
            candidate_row_count=1,
            candidate_error=None,
            reference_error=None,
        )
    assert out.verdict == "pass"
    assert out.skipped_judge is False
    assert out.judge_tokens_in == 4200
    assert out.judge_tokens_out == 32
    invoke_mock.assert_called_once()


def test_parse_judge_cli_output_reads_agy_response():
    raw = json.dumps(
        {
            "status": "SUCCESS",
            "response": '{"strict_verdict": "pass", "analytical_verdict": "pass", "reason": "ok"}',
            "usage": {"input_tokens": 1200, "output_tokens": 15},
        }
    ).encode()
    outcome = parse_judge_cli_output(raw)
    assert outcome.verdict == "pass"
    assert outcome.analytical_verdict == "pass"
    assert outcome.reason == "ok"
    assert outcome.tokens_in == 1200
    assert outcome.tokens_out == 15


def test_score_sync_calls_agy_cli():
    judge = LLMJudge(runner="agy_cli", model="claude-sonnet-5-5-medium")
    agy_stdout = json.dumps(
        {
            "status": "SUCCESS",
            "structured_output": {
                "strict_verdict": "pass",
                "analytical_verdict": "pass",
                "reason": "Matches.",
            },
            "usage": {"input_tokens": 500, "output_tokens": 20},
        }
    ).encode()
    with patch("cube.scoring.llm_judge.subprocess.run") as run_mock:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = agy_stdout
        out = judge.score_sync(
            question="q",
            schema_text="s",
            expected_sql="SELECT 1",
            expected_rows=[[1]],
            expected_row_count=1,
            candidate_sql="SELECT 1",
            candidate_rows=[[1]],
            candidate_row_count=1,
            candidate_error=None,
            reference_error=None,
        )
    assert out.verdict == "pass"
    assert out.judge_tokens_in == 500
    cmd = run_mock.call_args.args[0]
    assert cmd[0] == "agy"
    assert "--model" in cmd and "claude-sonnet-5-5-medium" in cmd
    assert "-p" in cmd
