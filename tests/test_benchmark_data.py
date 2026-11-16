from __future__ import annotations

from cube.types import BenchmarkQuestion, load_benchmark


def test_load_benchmark_parses_100_questions(repo_root):
    qs = load_benchmark(repo_root / "datasets" / "clickhouse" / "benchmark.json")
    assert isinstance(qs, list)
    assert len(qs) == 100
    for q in qs:
        assert isinstance(q, BenchmarkQuestion)
        assert q.id >= 1 and q.id <= 100
        assert q.level in {"simple", "medium", "complex", "advanced", "expert"}
        assert q.question.strip()
        assert q.expected_sql.strip()


def test_question_ids_are_sequential(repo_root):
    qs = load_benchmark(repo_root / "datasets" / "postgres" / "benchmark.json")
    ids = [q.id for q in qs]
    assert ids == list(range(1, 101))
