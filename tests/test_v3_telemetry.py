import json

from repofix.agent import RepoFixAgent
from repofix.harness.config import HarnessConfig
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.telemetry import TerminationReason


def test_tokens_timings_events_summary_and_missing_cache(tmp_path):
    model = FakeModelClient(
        [
            {
                "calls": [("bash", {"command": "python -m pytest"})],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
            {"calls": [("submit", {})]},
        ]
    )
    agent = RepoFixAgent(
        FakeEnv(), "task", tmp_path / "t.jsonl", "", "test", HarnessConfig.for_profile("v3"), model
    )
    result = agent.run()
    assert result.status in TerminationReason and result.cache_hit_tokens is None
    records = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    step = next(r for r in records if r["type"] == "step")
    assert step["prompt_tokens"] == 10 and "model_latency_seconds" in step
    assert "duration_seconds" in step["observation"][0]
    assert any(e["type"] == "permission" for e in step["events"])
    assert json.loads((tmp_path / "summary.json").read_text())["termination_reason"] == "submitted"


def test_budget_exhaustion_does_not_execute_response_tools(tmp_path):
    model = FakeModelClient(
        [{"calls": [("bash", {"command": "must-not-run"})], "usage": {"prompt_tokens": 1000000}}]
    )
    env = FakeEnv()
    result = RepoFixAgent(
        env,
        "task",
        tmp_path / "t",
        "",
        "test",
        HarnessConfig.for_profile("v3", max_cost_usd=0.00001),
        model,
    ).run()
    assert result.status == "max_cost" and "must-not-run" not in env.executed
