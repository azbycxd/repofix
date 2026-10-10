"""Independent OS processes, real SIGKILL, recreated Docker, hash-checked restore."""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import docker
import pytest

from repofix.agent import RepoFixAgent
from repofix.env import DockerEnv
from repofix.harness.checkpoint_v4 import V4CheckpointStore
from repofix.harness.config import HarnessConfig
from repofix.harness.model import FakeModelClient
from repofix.harness.state import RunState
from repofix.harness.workspace import RepoFiles, fingerprint
from v4_helpers import artifact_dir, v4_docker_image

pytestmark = pytest.mark.docker
FIXTURE = Path(__file__).with_name("v4_process_fixture.py")


@pytest.mark.parametrize(
    "phase,corruption",
    [
        ("pre_dispatch", None),
        ("post_edit", None),
        ("mid_write", None),
        ("active_job", None),
        ("post_edit", "manifest"),
        ("post_edit", "blob"),
    ],
)
def test_real_process_kill_and_independent_resume(
    tmp_path, v4_docker_image, phase, corruption, record_property
):
    folder = artifact_dir(tmp_path)
    argv = [
        sys.executable,
        str(FIXTURE),
        "run",
        "--phase",
        phase,
        "--image",
        v4_docker_image.image,
        "--folder",
        str(folder),
    ]
    client = docker.from_env()
    old = None
    with (folder / "killed-process.log").open("wb") as log:
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ))
        try:
            deadline = time.monotonic() + 90
            ready = False
            while time.monotonic() < deadline:
                if (folder / "container.json").exists() and old is None:
                    old = client.containers.get(
                        json.loads((folder / "container.json").read_text())["id"]
                    )
                if phase == "mid_write" and old is not None:
                    ready = old.exec_run(["test", "-f", "/tmp/v4-mid-write"]).exit_code == 0
                else:
                    ready = (folder / "kill-ready.json").exists()
                if ready or process.poll() is not None:
                    break
                time.sleep(0.1)
            assert ready and process.poll() is None, (folder / "killed-process.log").read_text()
            process.kill()
            assert process.wait(timeout=10) == -signal.SIGKILL
            if phase == "mid_write":
                assert b"VALUE = 2" in old.exec_run(["cat", "/testbed/module.py"]).output
            if corruption:
                latest = sorted((folder / "checkpoints").glob("generation-*.json"))[-1]
                target = (
                    latest
                    if corruption == "manifest"
                    else latest.parent / json.loads(latest.read_text())["workspace"]
                )
                target.write_text("injected corruption")
            argv[2] = "resume"
            completed = subprocess.run(argv, capture_output=True, timeout=90)
            (folder / "resume-process.stdout").write_bytes(completed.stdout)
            (folder / "resume-process.stderr").write_bytes(completed.stderr)
            assert completed.returncode == 0, completed.stderr.decode()
            data = json.loads((folder / "resumed.json").read_text())
            state = data["state"]
            assert state["step"] == 1 and state["budget"]["provider_calls"] == 1
            assert state["budget"]["estimated_cost"] > 0 and state["budget"]["prompt_tokens"] == 100
            assert data["workspace_signature"] == data["manifest"]["workspace_signature"]
            messages = [m for m in state["messages"] if m.get("role") == "tool"]
            assert len(messages) == 1 and messages[0]["tool_call_id"] == "call_1_0"
            if phase in {"pre_dispatch", "mid_write"} or corruption:
                assert "INTERRUPTED_UNKNOWN" in messages[0]["content"] and not data["patch"]
            elif phase == "post_edit":
                assert "+VALUE = 2" in data["patch"]
            if phase == "active_job":
                assert (
                    state["metadata"]["v4"]["background_jobs"][0]["state"] == "LOST_AFTER_RESTART"
                )
            with pytest.raises(docker.errors.NotFound):
                client.containers.get(old.id)
            (folder / "fault.json").write_text(
                json.dumps(
                    {
                        "phase": phase,
                        "corruption": corruption,
                        "process_exit": process.returncode,
                        "resume_exit": completed.returncode,
                        "old_container_removed": True,
                        "state_matches_snapshot": True,
                    },
                    indent=2,
                )
            )
            record_property("recovery_expected", phase + ("-" + corruption if corruption else ""))
            record_property("recovery_correct", True)
            record_property("execution_backend", "real-Docker+SIGKILL+independent-process")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if old is not None:
                try:
                    old.remove(force=True)
                except docker.errors.NotFound:
                    pass
            client.close()


@pytest.mark.parametrize("fault", ["oversize", "disk"])
def test_real_docker_snapshot_refusal_prevents_mutation(
    tmp_path, v4_docker_image, fault, record_property
):
    with DockerEnv("v4-refusal", v4_docker_image.image, uuid.uuid4().hex[:12]) as env:
        config = HarnessConfig.for_profile(
            "v4", checkpoint_max_bytes=1 if fault == "oversize" else 65536
        )
        agent = RepoFixAgent(
            env,
            "fixture",
            artifact_dir(tmp_path) / "trace.jsonl",
            "",
            "fixture",
            config,
            FakeModelClient([{"calls": [("bash", {"command": "echo bad > /testbed/module.py"})]}]),
        )
        if fault == "disk":
            with patch(
                "repofix.harness.checkpoint_v4.durable_write",
                side_effect=OSError("injected ENOSPC"),
            ):
                result = agent.run()
        else:
            result = agent.run()
        assert result.status == "checkpoint_error" and not result.patch
        assert env.execute("cat module.py").output == "VALUE = 1\n"
        record_property("recovery_expected", "fail-closed-" + fault)
        record_property("recovery_correct", True)
        record_property("execution_backend", "real-Docker")


def test_actual_python36_helpers_edit_rollback_snapshot(tmp_path, record_property):
    image = "swebench/sweb.eval.x86_64.django_1776_django-13343:latest"
    folder = artifact_dir(tmp_path)
    with DockerEnv("v4-py36", image, uuid.uuid4().hex[:12]) as env:
        env.v4_helpers = True
        version = env.execute("python --version").output.strip()
        assert version.startswith("Python 3.6"), version
        files = RepoFiles(env)
        files.apply({"v4_compat_probe.py": "VALUE = 1\n"})
        assert files.read("v4_compat_probe.py") == "VALUE = 1\n"
        with pytest.raises(RuntimeError):
            files.apply({"v4_compat_probe.py": "broken(\n"})
        assert files.read("v4_compat_probe.py") == "VALUE = 1\n"
        assert "v4_compat_probe.py" in fingerprint(env)
        store = V4CheckpointStore(folder)
        event = store.save(RunState(), env)
        files.apply({"v4_compat_probe.py": "VALUE = 2\n"})
        store.resume(env)
        assert files.read("v4_compat_probe.py") == "VALUE = 1\n"
        (folder / "python36.json").write_text(
            json.dumps({"python": version, "checkpoint": event}, indent=2)
        )
        record_property("execution_backend", "real-Docker-Python3.6")
        record_property("recovery_correct", True)
