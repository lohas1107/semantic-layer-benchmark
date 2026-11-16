"""Filesystem storage layout for experiment runs (runs/<run_id>/...)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from cube.config import REPO_ROOT


class RunStore:
    """Manages reading/writing artifacts under runs/<run_id>/."""

    def __init__(self, run_id: str, *, runs_root: Path | None = None) -> None:
        self.run_id = run_id
        self.runs_root = runs_root or (REPO_ROOT / "runs")
        self.root = self.runs_root / run_id

    @property
    def experiment_snapshot_path(self) -> Path:
        return self.root / "experiment.yaml"

    @property
    def reference_dir(self) -> Path:
        return self.root / "reference"

    def system_dir(self, system_id: str) -> Path:
        return self.root / "systems" / system_id

    def scoring_dir(self, system_id: str) -> Path:
        return self.root / "scoring" / system_id

    @staticmethod
    def question_path(directory: Path, question_id: int) -> Path:
        return directory / f"q{question_id:02d}.json"

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.reference_dir.mkdir(parents=True, exist_ok=True)

    def save_experiment_snapshot(self, yaml_path: Path) -> None:
        self.ensure_dirs()
        if yaml_path.resolve() != self.experiment_snapshot_path.resolve():
            shutil.copy2(yaml_path, self.experiment_snapshot_path)

    def save_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def load_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def discover_scored_systems(self) -> list[str]:
        base = self.root / "scoring"
        if not base.exists():
            return []
        return [
            d.name
            for d in sorted(base.iterdir())
            if d.is_dir() and any(d.glob("q*.json"))
        ]
