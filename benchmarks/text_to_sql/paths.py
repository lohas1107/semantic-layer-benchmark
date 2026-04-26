"""Filesystem path helpers for run artifacts."""
from __future__ import annotations

from pathlib import Path

from benchmarks.text_to_sql.config import REPO_ROOT


def run_dir(run_id: str, *, root: Path | None = None) -> Path:
    return (root or REPO_ROOT) / "runs" / run_id


def reference_dir(run_id: str, *, root: Path | None = None) -> Path:
    return run_dir(run_id, root=root) / "reference"


def scoring_dir(run_id: str, system: str, *, root: Path | None = None) -> Path:
    return run_dir(run_id, root=root) / "scoring" / system


def question_path(dir_: Path, question_id: int) -> Path:
    return dir_ / f"q{question_id:02d}.json"


def ensure(dir_: Path) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    return dir_
