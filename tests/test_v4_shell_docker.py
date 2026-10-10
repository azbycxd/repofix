import json
import time
import uuid
from contextlib import closing

import docker
import pytest

from repofix.agent import RepoFixAgent
from repofix.env import DockerEnv
from repofix.harness.config import HarnessConfig
from repofix.harness.model import FakeModelClient
from repofix.harness.tools.shell import ShellTools
from v4_helpers import artifact_dir, call, make_runtime, v4_docker_image

pytestmark = pytest.mark.docker


@pytest.mark.parametrize("background", [False, True])
def test_real_pipefail_and_v3_unchanged(tmp_path, v4_docker_image, background, record_property):
    with DockerEnv("v4-pipe", v4_docker_image.image, uuid.uuid4().hex[:12]) as env:
        legacy = ShellTools(env, HarnessConfig.for_profile("v3", background_shell=background))
        assert legacy.bash({"command": "false | tee /tmp/v3-pipe"}).exit_code == 0
        runtime = make_runtime(tmp_path, env, background_shell=background)
        result = call(runtime, "bash", {"command": "false | tee /tmp/v4-pipe"})
        assert result.exit_code == 1 and result.metadata["pipeline_failure"]
        assert result.metadata["execution_state"] == "EXITED"
        assert runtime.evidence_policy.current()["state"] == "UNVERIFIED"
        grep = call(runtime, "bash", {"command": "grep missing module.py"})
        assert grep.exit_code == 1 and grep.metadata["benign_exit"]
        sigpipe = call(runtime, "bash", {"command": "yes | head -n 1"})
        assert sigpipe.exit_code == 141 and "SIGPIPE" in sigpipe.content
        record_property("expected_validation", "UNVERIFIED")
        record_property("observed_validation", "UNVERIFIED")
        record_property("failure_injection", True)
        record_property("execution_backend", "real-Docker")


def test_real_job_timeout_poll_kill_limit_and_resource_inspect(
    tmp_path, v4_docker_image, record_property
):
    folder = artifact_dir(tmp_path)
    with DockerEnv(
        "v4-jobs", v4_docker_image.image, uuid.uuid4().hex[:12], sandbox_hardening=True
    ) as env:
        runtime = make_runtime(tmp_path, env)
        first = call(runtime, "bash", {"command": "sleep 30", "timeout": 1})
        assert first.metadata["execution_state"] == "RUNNING" and first.exit_code is None
        second = call(runtime, "bash", {"command": "sleep 30", "run_in_background": True})
        third = call(runtime, "bash", {"command": "sleep 30", "run_in_background": True})
        assert second.metadata["execution_state"] == "RUNNING" and "limit" in third.content
        polled = call(runtime, "job_output", {"job_id": first.metadata["job_id"]})
        assert polled.metadata["execution_state"] == "RUNNING"
        cancelled = call(runtime, "job_kill", {"job_id": first.metadata["job_id"]})
        assert cancelled.metadata["execution_state"] == "CANCELLED"
        runtime.cleanup()
        assert not any(runtime.jobs.output(j)["still_running"] for j in runtime.jobs.jobs)
        env.container.reload()
        host = env.container.attrs["HostConfig"]
        resources = {
            key: host[key]
            for key in ["NetworkMode", "PidsLimit", "Memory", "NanoCpus", "CapDrop", "SecurityOpt"]
        }
        assert resources["NetworkMode"] == "none" and resources["PidsLimit"] == 512
        assert resources["Memory"] == 4 * 1024**3 and resources["NanoCpus"] == 2_000_000_000
        assert "ALL" in resources["CapDrop"] and "no-new-privileges" in resources["SecurityOpt"]
        (folder / "docker-inspect.json").write_text(json.dumps(resources, indent=2))
        container_id = env.container.id
    with closing(docker.from_env()) as client:
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(container_id)
    record_property("cleanup_expected", "no-tracked-process-or-container")
    record_property("cleanup_correct", True)
    record_property("execution_backend", "real-Docker")


@pytest.mark.parametrize("ending", ["deadline", "interrupt", "provider", "submitted"])
def test_real_terminal_cleanup(tmp_path, v4_docker_image, ending, record_property):
    with DockerEnv("v4-end", v4_docker_image.image, uuid.uuid4().hex[:12]) as env:
        config = HarnessConfig.for_profile(
            "v4", task_deadline_seconds=2 if ending == "deadline" else 60, checkpointing=False
        )
        script = (
            [{"calls": [("bash", {"command": "sleep 30", "timeout": 10})]}]
            if ending == "deadline"
            else [
                {"calls": [("bash", {"command": "sleep 30", "run_in_background": True})]},
                KeyboardInterrupt()
                if ending == "interrupt"
                else RuntimeError("injected provider outage"),
            ]
        )
        if ending == "submitted":
            script = [
                {"calls": [("run_tests", {"targets": ["tests/test_pass.py"]})]},
                {"calls": [("bash", {"command": "sleep 30", "run_in_background": True})]},
                {"calls": [("submit", {})]},
            ]
        agent = RepoFixAgent(
            env,
            "fixture",
            artifact_dir(tmp_path) / "trace.jsonl",
            "",
            "fixture",
            config,
            FakeModelClient(script),
        )
        result = agent.run()
        assert (
            result.status
            == {
                "deadline": "task_deadline",
                "interrupt": "interrupted",
                "provider": "provider_error",
                "submitted": "submitted",
            }[ending]
        )
        assert any(
            e["type"] == "job_cleanup" and e["status"] == "CANCELLED" for e in agent.state.events
        )
        container_id = env.container.id
    with closing(docker.from_env()) as client:
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(container_id)
    record_property("cleanup_correct", True)
    record_property("execution_backend", "real-Docker")
