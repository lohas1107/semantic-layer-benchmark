from __future__ import annotations

from types import SimpleNamespace

from cube.runners.agentic_local import AgenticLocalRunner
from cube.runners.openai_compatible import OpenAICompatibleRunner
from cube.types import BenchmarkQuestion, ExecResult, RunnerRequest


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **_kwargs):
        resp = self._responses[self.calls]
        self.calls += 1
        return resp


def _fake_client(responses):
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(responses)))


class _FakeEngine:
    name = "postgres"
    dialect = "PostgreSQL"

    def __init__(self):
        self.executed: list[str] = []

    def execute(self, sql: str, *, timeout_s: int = 30, row_cap: int = 100) -> ExecResult:
        self.executed.append(sql)
        return ExecResult(rows=[[42]], columns=["cnt"], exec_ms=5)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass


def test_openai_compatible_runner_extracts_sql_and_usage():
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="```sql\nSELECT 42\n```"))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=15),
    )
    runner = OpenAICompatibleRunner(model="test-model", client=_fake_client([resp]))
    q = BenchmarkQuestion(id=1, level="simple", question="Count?", expected_sql="SELECT 42")
    res = runner.run(RunnerRequest(question=q, schema_text="DDL", dialect="PostgreSQL", mode="raw"))
    assert res.sql == "SELECT 42"
    assert res.tokens_in == 120
    assert res.tokens_out == 15
    assert res.error is None


def test_agentic_local_runner_executes_tool_loop():
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="run_sql", arguments='{"sql": "SELECT count(*) FROM t"}'),
    )
    r1 = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )
    r2 = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="```sql\nSELECT count(*) FROM t\n```",
                    tool_calls=[],
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=150, completion_tokens=10),
    )
    engine = _FakeEngine()
    runner = AgenticLocalRunner(model="test-model", client=_fake_client([r1, r2]))
    q = BenchmarkQuestion(id=1, level="simple", question="Count?", expected_sql="SELECT 42")
    res = runner.run(
        RunnerRequest(question=q, schema_text="DDL", dialect="PostgreSQL", mode="raw"),
        engine=engine,
    )
    assert res.sql == "SELECT count(*) FROM t"
    assert res.rounds == 2
    assert len(res.tool_calls) == 1
    assert res.tokens_in == 250
    assert res.tokens_out == 30
    assert engine.executed == ["SELECT count(*) FROM t"]
