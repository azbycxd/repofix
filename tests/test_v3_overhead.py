import json
from unittest.mock import patch

from repofix.agent import RepoFixAgent
from repofix.harness.checkpoint import CheckpointStore
from repofix.harness.config import HarnessConfig
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.tools.shell import JobManager
from repofix.search import BM25Index


def test_checkpoint_counts_and_step_events(tmp_path):
    calls = [
        {
            "calls": [
                ("view", {"path": "example.py", "start_line": 1, "end_line": 1}),
                ("grep", {"pattern": "VALUE"}),
            ]
        },
        {"calls": [("str_replace", {"path": "example.py", "old_str": "1", "new_str": "2"})]},
        {"calls": [("bash", {"command": "pytest"})]},
        {"calls": [("submit", {})]},
    ]
    agent = RepoFixAgent(
        FakeEnv(),
        "fix",
        tmp_path / "t",
        "",
        "test",
        HarnessConfig.for_profile("v3"),
        FakeModelClient(calls),
    )
    saved = []
    original = CheckpointStore.save

    def save(store, state, env):
        saved.append((state.step, len(state.pending_calls)))
        return original(store, state, env)

    with patch.object(CheckpointStore, "save", save):
        agent.run()
    assert saved == [
        (1, 2),
        (1, 0),  # before dispatch and step end; not after readonly tools
        (2, 1),
        (2, 0),
        (2, 0),  # before dispatch, after edit, step end
        (3, 1),
        (3, 0),
        (3, 0),  # before dispatch, after bash, step end
        (4, 1),
        (4, 0),  # before submit and step end
    ]
    records = [json.loads(line) for line in (tmp_path / "t").read_text().splitlines()]
    events = [e for r in records if r["type"] == "step" for e in r["events"]]
    assert events == agent.state.events


def test_child_reuses_bm25(tmp_path):
    model = FakeModelClient(
        [{"calls": [("explore", {"question": "where"})]}, {"calls": [("submit", {})]}]
    )
    agent = RepoFixAgent(
        FakeEnv(),
        "fix",
        tmp_path / "t",
        "",
        "test",
        HarnessConfig.for_profile("v3", subagents="both", hooks_enabled=False),
        model,
    )
    agent.subagent_client_factory = lambda mode: FakeModelClient(
        [{"calls": [("report", {"summary": "example.py"})]}]
    )
    with patch.object(BM25Index, "from_repository", wraps=BM25Index.from_repository) as build:
        agent.run()
    assert build.call_count == 1


def test_poll_backoff_and_tail_only(monkeypatch):
    jobs = JobManager(FakeEnv())
    clock, sleeps, tails = [0.0], [], []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def output(job_id, tail_lines=None):
        tails.append(tail_lines)
        return {"still_running": True}

    monkeypatch.setattr("repofix.harness.tools.shell.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("repofix.harness.tools.shell.time.sleep", sleep)
    monkeypatch.setattr(jobs, "output", output)
    assert jobs.wait("job", 2.8)["still_running"]
    assert sleeps[:4] == [0.1, 0.2, 0.5, 1.0]
    assert max(sleeps) <= 1 and set(tails) == {100}
