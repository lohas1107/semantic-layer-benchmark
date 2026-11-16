"""N-system accuracy report with 95% Wilson CIs and pairwise exact McNemar tests."""
from __future__ import annotations

import json
import math
from itertools import combinations
from math import comb
from pathlib import Path

from rich.console import Console
from rich.table import Table

from cube.config import REPO_ROOT, load_experiment
from cube.storage.run_store import RunStore
from cube.types import load_benchmark

LEVELS = ("simple", "medium", "complex", "advanced", "expert")
PAPER_RUN_ROOT = REPO_ROOT / "archive" / "paper-2026-04-22" / "published-runs"

_PAPER_LABELS = {
    "claude_claude_opus_4_7_raw": "Opus 4.7 raw",
    "claude_claude_opus_4_7_semantic": "Opus 4.7 + Sem",
    "claude_claude_sonnet_4_6_raw": "Sonnet 4.6 raw",
    "claude_claude_sonnet_4_6_semantic": "Sonnet 4.6 + Sem",
    "codex_gpt_5_4_raw": "GPT-5.4 raw",
    "codex_gpt_5_4_semantic": "GPT-5.4 + Sem",
}


def wilson_ci(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def mcnemar_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(comb(n, i) * 0.5**n for i in range(k + 1))
    return min(1.0, p * 2)


def sig_marker(p: float) -> str:
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


def _load_verdicts(scoring_dir: Path, key: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for f in sorted(scoring_dir.glob("q*.json")):
        qid = int(f.stem[1:])
        out[qid] = str(json.loads(f.read_text(encoding="utf-8")).get(key, "fail"))
    return out


def _load_system_meta(sys_dir: Path, key: str) -> dict[int, float]:
    if not sys_dir.exists():
        return {}
    out: dict[int, float] = {}
    for f in sorted(sys_dir.glob("q*.json")):
        val = json.loads(f.read_text(encoding="utf-8")).get(key)
        if isinstance(val, (int, float)):
            out[int(f.stem[1:])] = float(val)
    return out


def print_report(
    run_id: str,
    *,
    runs_root: Path | None = None,
    dataset_path: Path | None = None,
    metric: str = "analytical_verdict",
    include_paper: bool = False,
    console: Console | None = None,
) -> int:
    con = console or Console()
    store = RunStore(run_id, runs_root=runs_root)
    systems = store.discover_scored_systems()
    if not systems:
        con.print(f"[red]No scoring data under {store.root / 'scoring'}[/red]")
        return 1

    labels: dict[str, str] = {s: s for s in systems}
    resolved_dataset = dataset_path
    if store.experiment_snapshot_path.exists():
        spec = load_experiment(store.experiment_snapshot_path)
        if resolved_dataset is None:
            resolved_dataset = spec.dataset.path
        for sys_cfg in spec.systems:
            labels[sys_cfg.id] = sys_cfg.display_label

    if resolved_dataset is None:
        resolved_dataset = REPO_ROOT / "datasets" / "postgres" / "benchmark.json"

    questions = load_benchmark(resolved_dataset)
    q_by_id = {q.id: q for q in questions}

    verdicts: dict[str, dict[int, str]] = {
        s: _load_verdicts(store.scoring_dir(s), metric) for s in systems
    }
    meta_dirs: dict[str, Path] = {s: store.system_dir(s) for s in systems}

    if include_paper and (PAPER_RUN_ROOT / "scoring").exists():
        for d in sorted((PAPER_RUN_ROOT / "scoring").iterdir()):
            if d.is_dir() and any(d.glob("q*.json")):
                ps = f"paper:{d.name}"
                systems.append(ps)
                labels[ps] = _PAPER_LABELS.get(d.name, d.name)
                verdicts[ps] = _load_verdicts(d, metric)
                meta_dirs[ps] = PAPER_RUN_ROOT / d.name

    # Headline table
    table = Table(title=f"Run '{run_id}' ({metric})")
    table.add_column("System", style="bold")
    table.add_column("Pass / N", justify="right")
    table.add_column("Accuracy", justify="right")
    table.add_column("95% Wilson CI", justify="right")
    table.add_column("Mean Latency", justify="right")
    table.add_column("Mean Tok In", justify="right")
    table.add_column("Mean Tok Out", justify="right")

    for s in systems:
        vmap = verdicts[s]
        gradable = [qid for qid, v in vmap.items() if v != "excluded"]
        n = len(gradable)
        passed = sum(1 for qid in gradable if vmap[qid] == "pass")
        acc = passed / n if n else 0.0
        lo, hi = wilson_ci(acc, n)
        lat = _load_system_meta(meta_dirs[s], "latency_ms_gen")
        tin = _load_system_meta(meta_dirs[s], "tokens_in")
        tout = _load_system_meta(meta_dirs[s], "tokens_out")
        mean_lat = f"{sum(lat.values()) / len(lat) / 1000:.1f} s" if lat else "-"
        mean_tin = f"{sum(tin.values()) / len(tin):,.0f}" if tin else "-"
        mean_tout = f"{sum(tout.values()) / len(tout):,.0f}" if tout else "-"
        table.add_row(
            labels[s],
            f"{passed}/{n}",
            f"{acc * 100:.1f}%",
            f"[{lo * 100:.1f}%, {hi * 100:.1f}%]",
            mean_lat,
            mean_tin,
            mean_tout,
        )
    con.print(table)

    # Level breakdown
    lvl_table = Table(title="Accuracy by Difficulty Level")
    lvl_table.add_column("System", style="bold")
    for lvl in LEVELS:
        lvl_table.add_column(lvl.capitalize(), justify="right")
    for s in systems:
        vmap = verdicts[s]
        row = [labels[s]]
        for lvl in LEVELS:
            qids = [
                qid
                for qid, v in vmap.items()
                if v != "excluded" and qid in q_by_id and q_by_id[qid].level == lvl
            ]
            if not qids:
                row.append("-")
            else:
                p = sum(1 for qid in qids if vmap[qid] == "pass")
                row.append(f"{p}/{len(qids)} ({p / len(qids) * 100:.0f}%)")
        lvl_table.add_row(*row)
    con.print(lvl_table)

    # Pairwise McNemar
    if len(systems) >= 2:
        mc_table = Table(title="Pairwise Exact McNemar Tests")
        mc_table.add_column("Pair")
        mc_table.add_column("A+/B-", justify="right")
        mc_table.add_column("A-/B+", justify="right")
        mc_table.add_column("p-value", justify="right")
        mc_table.add_column("Sig", justify="center")
        for a, b in combinations(systems, 2):
            va, vb = verdicts[a], verdicts[b]
            common = [
                qid
                for qid in va
                if qid in vb and va[qid] != "excluded" and vb[qid] != "excluded"
            ]
            ab = sum(1 for qid in common if va[qid] == "pass" and vb[qid] == "fail")
            ba = sum(1 for qid in common if va[qid] == "fail" and vb[qid] == "pass")
            pv = mcnemar_p(ab, ba)
            mc_table.add_row(
                f"{labels[a]} vs {labels[b]}",
                str(ab),
                str(ba),
                f"{pv:.4f}",
                sig_marker(pv),
            )
        con.print(mc_table)

    return 0
