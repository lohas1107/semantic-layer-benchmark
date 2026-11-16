from __future__ import annotations

from cube.storage import RunStore


def test_run_store_paths(tmp_path):
    store = RunStore("20260417-120000", runs_root=tmp_path)
    assert store.root == tmp_path / "20260417-120000"
    assert store.reference_dir.name == "reference"
    assert store.scoring_dir("gemma4__raw").parts[-2:] == ("scoring", "gemma4__raw")
    assert store.question_path(store.reference_dir, 7).name == "q07.json"
    assert store.question_path(store.reference_dir, 50).name == "q50.json"
