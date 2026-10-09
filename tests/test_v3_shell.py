import pytest

from repofix.agent import RepoFixAgent
from repofix.env import DockerEnv
from repofix.harness.config import HarnessConfig
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.runtime import Runtime
from repofix.harness.state import RunState
from repofix.harness.tools.shell import SHELL_ENV, JobManager, benign_exit


def test_timeout_background_poll_kill():
    env = FakeEnv(commands={"long": ("hello\nworld", None, True, 0)})
    jobs = JobManager(env)
    job = jobs.start("long")
    assert jobs.wait(job, 0.01)["still_running"]
    assert jobs.output(job, 1)["output"] == "world"
    assert jobs.kill(job)["exit_code"] == 143
    with pytest.raises(ValueError):
        jobs.output("missing")


def test_completed_and_benign():
    jobs = JobManager(FakeEnv(commands={"pytest": ("failed", 1, False, 0)}))
    job = jobs.start("pytest")
    assert not jobs.wait(job, 1)["still_running"]
    assert not benign_exit("python -m pytest", 1)
    assert not benign_exit("build", 1)
    assert SHELL_ENV["PYTHONUNBUFFERED"] == "1"


@pytest.mark.parametrize(
    "command, expected",
    [
        ("pytest -q", False),
        ("grep foo x", True),
        ("python test.py", False),
        ("echo pytest", False),
        ("env X=Y timeout 10 grep foo x", True),
        ("grep foo x | python test.py", False),
        ("python test.py | rg foo", True),
        ("git diff HEAD", True),
        ("git status", False),
        ("tox", False),
    ],
)
def test_benign_exit_uses_command_not_arguments(command, expected):
    assert benign_exit(command, 1) is expected


def test_shell_header(tmp_path):
    agent = RepoFixAgent(
        FakeEnv(commands={"grep missing": ("", 1, False, 0)}),
        "test",
        tmp_path / "t",
        "",
        "test",
        HarnessConfig.for_profile("v3"),
        FakeModelClient([]),
    )
    result = Runtime(agent, RunState()).execute(
        {"id": "1", "name": "bash", "arguments": '{"command":"grep missing"}'}
    )
    assert result.content.startswith("exit_code: 1 | duration:")
    assert "truncated: no | benign_exit" in result.content


@pytest.mark.docker
def test_docker_job_survives_wait_timeout():
    # Existing image only; opt-in test performs no pulls/builds.
    with DockerEnv("v3-job-test", "python:3.12-slim", "v3-integration") as env:
        jobs = JobManager(env)
        job = jobs.start("sleep 2; echo done")
        assert jobs.wait(job, 0.01)["still_running"]
        assert jobs.wait(job, 10)["exit_code"] == 0
