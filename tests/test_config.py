from __future__ import annotations

import pytest

from cube.config import ExperimentSpec, load_experiment, require_env, resolve_env


def test_load_gemma_experiments(repo_root):
    owui = load_experiment(repo_root / "experiments" / "gemma4-26b-pg-agentic-owui.yaml")
    assert isinstance(owui, ExperimentSpec)
    assert owui.engine.type == "postgres"
    assert owui.dataset.path.exists()
    assert owui.dataset.schema_path.exists()
    assert len(owui.systems) == 2
    assert owui.systems[0].runner == "openwebui"

    local = load_experiment(repo_root / "experiments" / "gemma4-26b-pg-agentic-local.yaml")
    assert local.systems[0].runner == "agentic_local"
    assert local.systems[1].mode == "semantic"


def test_resolve_and_require_env(monkeypatch):
    monkeypatch.setenv("CUBE_TEST_VAR", "hello")
    assert resolve_env("CUBE_TEST_VAR") == "hello"
    assert require_env("CUBE_TEST_VAR") == "hello"

    monkeypatch.delenv("CUBE_MISSING_VAR", raising=False)
    assert resolve_env("CUBE_MISSING_VAR", default="fallback") == "fallback"
    with pytest.raises(RuntimeError, match="CUBE_MISSING_VAR"):
        require_env("CUBE_MISSING_VAR")
