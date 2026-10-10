import json
from unittest.mock import patch

from repofix.agent import RepoFixAgent
from repofix.harness.config import HarnessConfig
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.progress import ProgressMonitor, normalize_observation
from repofix.harness.state import RunState
from repofix.search import BM25Index
from v4_helpers import artifact_dir, call, make_runtime, scripted_pytest


def run_script(tmp_path, script, env=None, **config):
    agent = RepoFixAgent(
        env or FakeEnv(),
        "fix public issue",
        artifact_dir(tmp_path) / "trace.jsonl",
        "",
        "fixture",
        HarnessConfig.for_profile("v4", **config),
        FakeModelClient(script),
    )
    result = agent.run()
    records = [json.loads(line) for line in agent.trace.path.read_text().splitlines()]
    return agent, result, [r for r in records if r["type"] == "progress"]


def test_repeated_grep_warn_replan_stuck_before_budget(tmp_path, record_property):
    script = [{"calls": [("grep", {"pattern": "VALUE"})]}] * 50
    with patch.object(BM25Index, "from_repository", wraps=BM25Index.from_repository) as build:
        agent, result, samples = run_script(tmp_path, script)
    assert result.status == "stuck" and not result.submitted and result.steps < 50
    assert [s["action"] for s in samples if s["action"]] == ["WARN", "REPLAN", "REPLAN", "STUCK"]
    assert build.call_count == 1
    assert result.reliability["progress"]["feedback_bytes"] > 0
    assert result.reliability["progress"]["duration_seconds"] >= 0
    record_property("stuck_expected", True)
    record_property("stuck_observed", result.status == "stuck")
    record_property("steps", result.steps)
    record_property("progress_seconds", result.reliability["progress"]["duration_seconds"])
    record_property("progress_feedback_bytes", result.reliability["progress"]["feedback_bytes"])


def test_disabled_monitor_preserves_step_limit(tmp_path):
    _, result, samples = run_script(
        tmp_path,
        [{"calls": [("grep", {"pattern": "VALUE"})]}] * 6,
        max_steps=6,
        progress_monitor=False,
    )
    assert result.status == "max_steps" and not samples


def test_natural_language_replans_do_not_reset_stagnation(tmp_path):
    script = [
        {
            "content": str(i),
            "calls": [("update_plan", {"steps": [{"step": str(i), "status": "in_progress"}]})],
        }
        for i in range(30)
    ]
    _, result, samples = run_script(tmp_path, script, progress_no_progress_steps=3)
    assert result.status == "stuck" and result.steps == 9
    assert sum(s["action"] == "REPLAN" for s in samples) == 2


def test_different_files_and_versions_are_progress(tmp_path, record_property):
    env = FakeEnv({f"f{i}.py": f"VALUE = {i}\n" for i in range(10)})
    script = [
        {"calls": [("view", {"path": f"f{i}.py", "start_line": 1, "end_line": 1})]}
        for i in range(10)
    ]
    _, result, samples = run_script(
        tmp_path, script, env, max_steps=10, progress_no_progress_steps=2
    )
    assert result.status == "max_steps" and all(s["signal"] == "NEW_EVIDENCE" for s in samples)
    record_property("stuck_expected", False)
    record_property("stuck_observed", result.status == "stuck")


def test_same_test_after_edit_and_new_file_version(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.env.argv_handler = scripted_pytest("fail", code=1)
    monitor = ProgressMonitor(runtime.config, runtime.env, runtime.state)

    def sample(name, args):
        runtime.state.step += 1
        result = call(runtime, name, args)
        c = {"name": name, "arguments": json.dumps(args)}
        return monitor.observe([c], [{"content": result.content, **result.metadata}])

    assert sample("run_tests", {"targets": ["example.py"]})["signal"] == "VALIDATION_CHANGED"
    for _ in range(4):
        assert sample("run_tests", {"targets": ["example.py"]})["signal"] == "SAME_RESULT"
    sample("view", {"path": "example.py", "start_line": 1, "end_line": 1})
    assert (
        sample("str_replace", {"path": "example.py", "old_str": "1", "new_str": "2"})["signal"]
        == "WORKSPACE_CHANGED"
    )
    runtime.env.argv_handler = scripted_pytest("pass")
    assert sample("run_tests", {"targets": ["example.py"]})["signal"] == "VALIDATION_CHANGED"
    assert not runtime.state.termination


def test_waiting_has_a_finite_poll_budget(tmp_path):
    runtime = make_runtime(
        tmp_path, progress_job_poll_budget=2, progress_no_progress_steps=1, progress_max_replans=1
    )
    monitor = ProgressMonitor(runtime.config, runtime.env, runtime.state)
    c = {"name": "job_output", "arguments": '{"job_id":"abc"}'}
    observation = {"content": "running", "still_running": True, "job_id": "abc"}
    assert monitor.observe([c], [observation])["signal"] == "WAITING_FOR_JOB"
    assert monitor.observe([c], [observation])["action"] is None
    assert monitor.observe([c], [observation])["action"] == "REPLAN"
    assert monitor.observe([c], [observation])["action"] == "STUCK"


def test_volatile_time_and_ids_are_normalized():
    left = "2026-10-10T11:00:01Z duration: 1.223s in 2.13s job_id=abcdefabcdef1234"
    right = "2026-10-10T11:02:51Z duration: 4.344s in 4.55s job_id=012345abcdef1234"
    assert normalize_observation(left) == normalize_observation(right)


def test_stuck_retains_unsubmitted_patch(tmp_path):
    script = [
        {"calls": [("view", {"path": "example.py", "start_line": 1, "end_line": 1})]},
        {"calls": [("str_replace", {"path": "example.py", "old_str": "1", "new_str": "2"})]},
    ] + [{"calls": [("grep", {"pattern": "VALUE"})]}] * 30
    _, result, _ = run_script(
        tmp_path, script, progress_no_progress_steps=2, progress_max_replans=1
    )
    assert result.status == "stuck" and not result.submitted and "+VALUE = 2" in result.patch
    assert result.reliability["judge_verdict"] == "NOT_JUDGED"
