from __future__ import annotations

from benchmarks.text_to_sql.paths import (
    question_path,
    reference_dir,
    run_dir,
    scoring_dir,
)


def test_run_dir_is_under_runs(tmp_path):
    d = run_dir("20260417-120000", root=tmp_path)
    assert d == tmp_path / "runs" / "20260417-120000"


def test_per_system_dirs(tmp_path):
    assert reference_dir("r1", root=tmp_path).name == "reference"
    assert scoring_dir("r1", "claude_claude_opus_4_7_raw", root=tmp_path).parts[-2:] == (
        "scoring",
        "claude_claude_opus_4_7_raw",
    )


def test_question_path_zero_padded(tmp_path):
    p = question_path(reference_dir("r1", root=tmp_path), 7)
    assert p.name == "q07.json"
    p50 = question_path(reference_dir("r1", root=tmp_path), 50)
    assert p50.name == "q50.json"
