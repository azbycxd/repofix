import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from repofix.env import DockerEnv, ExecutionResult
from repofix.harness.config import HarnessConfig
from repofix.harness.deadline import Deadline, DeadlineModel, TaskDeadline
from repofix.harness.fake import FakeEnv
from repofix.harness.state import RunState
from repofix.harness.tools.shell import JobManager, ShellTools
from test_v4_progress import run_script
from v4_helpers import call, make_runtime


def test_fake_background_running_cancelled_and_limit(tmp_path):
    env = FakeEnv(commands={"sleep": ExecutionResult("waiting", None, True, 0)})
    runtime = make_runtime(tmp_path, env)
    first = call(runtime, "bash", {"command": "sleep", "run_in_background": True})
    second = call(runtime, "bash", {"command": "sleep", "run_in_background": True})
    assert first.metadata["execution_state"] == second.metadata["execution_state"] == "RUNNING"
    blocked = call(runtime, "bash", {"command": "sleep", "run_in_background": True})
    assert blocked.metadata["execution_state"] == "TOOL_ERROR" and "limit" in blocked.content
    cancelled = call(runtime, "job_kill", {"job_id": first.metadata["job_id"]})
    assert cancelled.metadata["execution_state"] == "CANCELLED"
    runtime.cleanup()
    assert all(e["status"] == "CANCELLED" for e in runtime.state.metadata["v4"]["cleanup_events"])


@pytest.mark.parametrize("background", [False, True])
def test_missing_exit_is_environment_error_not_success(tmp_path, background):
    env = FakeEnv(commands={"unknown": ExecutionResult("lost", None, False, 0)})
    runtime = make_runtime(tmp_path, env, background_shell=background)
    result = call(runtime, "bash", {"command": "unknown"})
    assert result.metadata["execution_state"] == "ENV_ERROR" and result.exit_code is None
    assert runtime.evidence_policy.current()["state"] == "UNVERIFIED"


def test_grep_benign_annotation_cannot_turn_failure_into_validation(tmp_path):
    env = FakeEnv(commands={"grep missing a.py": ExecutionResult("", 1, False, 0)})
    runtime = make_runtime(tmp_path, env)
    result = call(runtime, "bash", {"command": "grep missing a.py"})
    assert result.exit_code == 1 and result.metadata["benign_exit"]
    assert runtime.evidence_policy.current()["state"] == "UNVERIFIED"


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), RuntimeError("provider offline")])
def test_terminal_errors_cleanup_jobs(tmp_path, failure):
    env = FakeEnv(commands={"wait": ExecutionResult("", None, True, 0)})
    agent, result, _ = run_script(
        tmp_path,
        [{"calls": [("bash", {"command": "wait", "run_in_background": True})]}, failure],
        env,
    )
    assert result.status in {"interrupted", "provider_error"}
    assert any(
        e["type"] == "job_cleanup" and e["status"] == "CANCELLED" for e in agent.state.events
    )


def test_deadline_model_no_late_tools_and_unknown_usage():
    state = RunState()
    config = HarnessConfig.for_profile("v4", task_deadline_seconds=0.03)
    model = SimpleNamespace(complete=lambda *args: time.sleep(0.2))
    with pytest.raises(TaskDeadline):
        DeadlineModel(model, Deadline(config, state)).complete([], [], config)
    assert state.metadata["v4"]["provider_usage_incomplete"]
    assert state.budget.provider_calls == 0  # no completed response, NOT a billable-call count


def test_deadline_is_persisted_not_reset_on_resume():
    config = HarnessConfig.for_profile("v4")
    state = RunState(metadata={"v4": {"deadline_epoch": time.time() - 1}})
    with pytest.raises(TaskDeadline):
        Deadline(config, state).check()


def test_cleanup_daemon_failure_is_an_event_not_silent():
    env = DockerEnv("fixture", "fixture", "fixture")
    env.v4_helpers = True
    env.container = Mock(
        id="owned",
        stop=Mock(side_effect=OSError("daemon gone")),
        remove=Mock(side_effect=OSError("daemon gone")),
    )
    env.client = Mock()
    events = []
    env.lifecycle_sink = events.append
    env.close()
    assert [e["phase"] for e in events if e["type"] == "cleanup_error"] == ["stop", "remove"]
    assert all(e["status"] == "ERROR" for e in events)


def test_job_cleanup_failure_is_reported():
    jobs = JobManager(FakeEnv(commands={"wait": ExecutionResult("", None, True, 0)}), reliable=True)
    jobs.start("wait")
    jobs.kill = Mock(side_effect=OSError("injected kill error"))
    assert jobs.cleanup()[0]["type"] == "cleanup_error"
