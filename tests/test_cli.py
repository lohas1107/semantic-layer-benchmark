from __future__ import annotations

import json
import os

import pytest

from cube.cli import _lifecycle_guard, main
from cube.config import load_experiment


def test_cli_report_smoke(tmp_path, capsys):
    runs_root = tmp_path / "runs"
    scoring_dir = runs_root / "r1" / "scoring" / "sys_a"
    sys_dir = runs_root / "r1" / "systems" / "sys_a"
    scoring_dir.mkdir(parents=True)
    sys_dir.mkdir(parents=True)

    (scoring_dir / "q01.json").write_text(
        json.dumps({"id": 1, "level": "simple", "analytical_verdict": "pass"})
    )
    (sys_dir / "q01.json").write_text(
        json.dumps({"id": 1, "latency_ms_gen": 1200, "tokens_in": 500, "tokens_out": 50})
    )

    rc = main(["report", "r1", "--runs-root", str(runs_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sys_a" in out
    assert "100.0%" in out


def test_lifecycle_guard_injects_env_and_always_runs_after_run(tmp_path, repo_root):
    marker = tmp_path / "after.txt"
    spec = load_experiment(repo_root / "experiments" / "gemma4-26b-pg-agentic-local.yaml")
    spec.lifecycle.before_run = "echo CUBE_HOOK_TEST=injected_val"
    spec.lifecycle.after_run = f"echo stopped > {marker}"

    with pytest.raises(RuntimeError, match="boom"), _lifecycle_guard(spec):
        assert os.environ.get("CUBE_HOOK_TEST") == "injected_val"
        raise RuntimeError("boom")

    assert marker.read_text().strip() == "stopped"
    os.environ.pop("CUBE_HOOK_TEST", None)
