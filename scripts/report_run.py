"""N-system accuracy report with Wilson CIs and pairwise McNemar tests.

Auto-discovers any subdirectory under runs/<run-id>/scoring/ that has *.json
verdict files, so adding a new model is just running its runner — no code
change required to make it appear in the report.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import json
import math
from itertools import combinations
from math import comb

from benchmarks.text_to_sql.benchmark_data import load_benchmark
from benchmarks.text_to_sql.config import REPO_ROOT

BENCHMARK_JSON = REPO_ROOT / "text_to_sql_benchmark.json"
LEVELS = ("simple", "medium", "complex", "advanced", "expert")


def discover_systems(runs_root: Path, run_id: str) -> list[str]:
    """All scoring/<system>/ dirs that have at least one *.json file."""
    base = runs_root / run_id / "scoring"
    if not base.exists():
        return []
    out: list[str] = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and any(d.glob("*.json")):
            out.append(d.name)
    return out


def label_for(system: str) -> str:
    """Pretty name for known slugs; fall back to the slug itself."""
    pretty = {
        "claude_claude_opus_4_7_raw": "Opus 4.7 raw",
        "claude_claude_opus_4_7_semantic": "Opus 4.7 + Sem",
        "claude_claude_sonnet_4_6_raw": "Sonnet 4.6 raw",
        "claude_claude_sonnet_4_6_semantic": "Sonnet 4.6 + Sem",
        "codex_gpt_5_4_raw": "GPT-5.4 raw",
        "codex_gpt_5_4_semantic": "GPT-5.4 + Sem",
    }
    return pretty.get(system, system)


def load_scores(runs_root: Path, run_id: str, system: str, key: str = "analytical_verdict") -> dict[int, str]:
    d = runs_root / run_id / "scoring" / system
    return {
        int(f.stem[1:]): json.loads(f.read_text(encoding="utf-8")).get(key)
        for f in d.glob("*.json")
    }


def load_meta(runs_root: Path, run_id: str, system: str, key: str) -> dict[int, object]:
    d = runs_root / run_id / system
    if not d.exists():
        return {}
    return {
        int(f.stem[1:]): json.loads(f.read_text(encoding="utf-8")).get(key)
        for f in d.glob("*.json")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-root", default="runs",
                        help="Root directory containing per-run dirs (default 'runs/'; "
                             "use 'published-runs' to inspect the paper's reference run).")
    parser.add_argument("--metric", default="analytical_verdict",
                        choices=("analytical_verdict", "verdict"),
                        help="Which judge verdict to report on (default analytical).")
    args = parser.parse_args(argv)

    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = REPO_ROOT / runs_root

    systems = discover_systems(runs_root, args.run_id)
    if not systems:
        print(f"No scoring data under {runs_root}/{args.run_id}/scoring/")
        return 1

    questions = load_benchmark(BENCHMARK_JSON)
    data = {s: load_scores(runs_root, args.run_id, s, args.metric) for s in systems}
    all_ids = sorted(
        set.intersection(
            *[{q for q, v in d.items() if v != "excluded"} for d in data.values() if d]
        )
    )
    n = len(all_ids)
    print(f"\nRun {args.run_id}  —  metric: {args.metric}  —  n={n}\n")

    # Headline table
    label_w = max(len(label_for(s)) for s in systems)
    print(f"{'System':<{label_w}}  {'Acc':>7}  {'95% CI':<18}  {'mean lat':>9}  {'tok in':>9}  {'tok out':>9}")
    print("-" * (label_w + 60))
    for s in systems:
        passes = sum(1 for q in all_ids if data[s].get(q) == "pass")
        p = passes / n if n > 0 else 0.0
        lo, hi = wilson_ci(p, n)
        lats = [v for v in load_meta(runs_root, args.run_id, s, "latency_ms_gen").values() if v]
        mean_lat = int(sum(lats) / len(lats)) if lats else 0
        tin = [v for v in load_meta(runs_root, args.run_id, s, "tokens_in").values() if v]
        tout = [v for v in load_meta(runs_root, args.run_id, s, "tokens_out").values() if v]
        mean_tin = sum(tin) // len(tin) if tin else 0
        mean_tout = sum(tout) // len(tout) if tout else 0
        print(
            f"{label_for(s):<{label_w}}  {p * 100:>6.1f}%  "
            f"[{lo * 100:>4.1f}%, {hi * 100:>4.1f}%]  {mean_lat:>7}ms  "
            f"{mean_tin:>9}  {mean_tout:>9}"
        )

    # By tier
    print(f"\nBy difficulty tier ({args.metric}):\n")
    header = f"{'Tier':<10} {'N':>4}  " + "  ".join(
        f"{label_for(s):>{label_w}}" for s in systems
    )
    print(header)
    for tier in LEVELS:
        ids = [q.id for q in questions if q.level == tier and q.id in all_ids]
        nt = len(ids)
        row = f"{tier:<10} {nt:>4}  "
        for s in systems:
            passes = sum(1 for qid in ids if data[s].get(qid) == "pass")
            pct = (passes / nt * 100) if nt > 0 else 0.0
            row += f"{pct:>{label_w - 1}.1f}%  "
        print(row.rstrip())

    # Pairwise McNemar matrix
    print("\nPairwise McNemar (b/c, two-sided exact p, sig):\n")
    cell_w = max(label_w, 16)
    print(f"{'':<{label_w}}  ", end="")
    for s in systems:
        print(f"{label_for(s)[:cell_w]:<{cell_w}}", end="")
    print()
    for s1 in systems:
        print(f"{label_for(s1):<{label_w}}  ", end="")
        for s2 in systems:
            if s1 == s2:
                print(f"{'  -':<{cell_w}}", end="")
                continue
            b = sum(1 for q in all_ids if data[s1].get(q) == "pass" and data[s2].get(q) == "fail")
            c = sum(1 for q in all_ids if data[s1].get(q) == "fail" and data[s2].get(q) == "pass")
            p = mcnemar_p(b, c)
            cell = f"{b}/{c} {sig_marker(p)}"
            print(f"{cell:<{cell_w}}", end="")
        print()

    # Within-model semantic vs raw shortcut: any pair where one slug is X_raw and
    # another is X_semantic, surface the McNemar test prominently.
    print("\nWithin-model semantic vs raw lifts (paired McNemar):\n")
    by_base: dict[str, dict[str, str]] = {}
    for s in systems:
        if s.endswith("_raw"):
            by_base.setdefault(s[:-4], {})["raw"] = s
        elif s.endswith("_semantic"):
            by_base.setdefault(s[:-9], {})["semantic"] = s
    for base, pair in sorted(by_base.items()):
        if "raw" not in pair or "semantic" not in pair:
            continue
        sem, raw = pair["semantic"], pair["raw"]
        b = sum(1 for q in all_ids if data[sem].get(q) == "pass" and data[raw].get(q) == "fail")
        c = sum(1 for q in all_ids if data[sem].get(q) == "fail" and data[raw].get(q) == "pass")
        p = mcnemar_p(b, c)
        sem_acc = sum(1 for q in all_ids if data[sem].get(q) == "pass") / n if n else 0
        raw_acc = sum(1 for q in all_ids if data[raw].get(q) == "pass") / n if n else 0
        delta = (sem_acc - raw_acc) * 100
        print(
            f"  {label_for(sem):<22} vs {label_for(raw):<18}  "
            f"b={b:>3}  c={c:>3}  p={p:.4f} {sig_marker(p):<3}  Δ={delta:+.1f} pp"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
