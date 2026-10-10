"""Explicit offline/real-Docker fixtures. None calls a real model."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from repofix.agent import RepoFixAgent
from repofix.env import ArgvExecutionResult
from repofix.harness.config import HarnessConfig
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.runtime import Runtime
from repofix.harness.state import RunState
from repofix.python_sandbox import PythonSandboxImage
from test_v3_workspace_backend import LocalHelperFixture


def artifact_dir(tmp_path):
    root = os.environ.get("V4_EVIDENCE_DIR")
    result = Path(root) / tmp_path.name if root else tmp_path / "evidence"
    result.mkdir(parents=True, exist_ok=True)
    return result


def make_runtime(tmp_path, env=None, **config):
    agent = RepoFixAgent(
        env or FakeEnv(),
        "repair public issue",
        artifact_dir(tmp_path) / "trajectory.jsonl",
        "",
        "offline-fixture",
        HarnessConfig.for_profile("v4", **config),
        FakeModelClient([]),
    )
    state = RunState()
    agent.state = state
    return Runtime(agent, state)


def call(runtime, name, args=None, identifier="call-1"):
    return runtime.execute({"id": identifier, "name": name, "arguments": json.dumps(args or {})})


def junit(kind="pass"):
    child = {"pass": "", "fail": '<failure message="assertion"/>', "skip": "<skipped/>"}.get(
        kind, ""
    )
    return (
        '<testsuites><testsuite tests="0"/></testsuites>'
        if kind == "empty"
        else f'<testsuites><testsuite tests="1"><testcase name="test_case">{child}</testcase></testsuite></testsuites>'
    )


def scripted_pytest(kind="pass", code=0, timed_out=False, mutate=False, missing=False):
    def handler(env, argv, timeout, cwd):
        assert cwd == "/testbed"
        if argv[0] == "mkdir":
            return ArgvExecutionResult("", "", 0, False, 0)
        assert argv[:3] == ["python", "-m", "pytest"]
        path = next(v.split("=", 1)[1] for v in argv if v.startswith("--junitxml="))
        if not missing:
            env.output_files[path] = junit(kind)
        if mutate:
            env.files["example.py"] = "VALUE = 42\n"
        return ArgvExecutionResult(
            "scripted fixture output", "", code, timed_out, 0.001, completed=code is not None
        )

    return handler


class LocalValidationEnv(LocalHelperFixture):
    """Real subprocess argv; only fixed container roots mapped to disposable paths."""

    def execute_argv(self, argv, timeout=180, cwd="/testbed"):
        assert cwd == "/testbed"
        argv = [
            v.replace("/testbed", str(self.root)).replace(
                "/tmp/repofix_validation", str(self.artifacts / "validation")
            )
            for v in argv
        ]
        if argv[0] == "python":
            argv[0] = sys.executable
        started = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                cwd=self.root,
                capture_output=True,
                text=True,
                env={**self.environment, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                timeout=timeout,
            )
            return ArgvExecutionResult(
                result.stdout, result.stderr, result.returncode, False, time.monotonic() - started
            )
        except subprocess.TimeoutExpired as exc:
            return ArgvExecutionResult(
                (exc.stdout or b"").decode(),
                (exc.stderr or b"").decode(),
                None,
                True,
                time.monotonic() - started,
                completed=False,
            )

    def read_validation_report(self, path, max_bytes=8 * 1024 * 1024):
        return (self.artifacts / "validation" / Path(path).name).read_bytes()


@pytest.fixture(scope="session")
def v4_docker_image(tmp_path_factory):
    repository = tmp_path_factory.mktemp("v4-trusted-pytest-fixture")
    (repository / "module.py").write_text("VALUE = 1\n")
    tests = repository / "tests"
    tests.mkdir()
    (tests / "test_pass.py").write_text(
        "from module import VALUE\ndef test_pass(): assert VALUE == 1\n"
    )
    (tests / "test_fail.py").write_text("def test_fail(): assert False, 'injected failure'\n")
    (tests / "test_skip.py").write_text("import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n")
    (tests / "test_timeout.py").write_text("import time\ndef test_timeout(): time.sleep(15)\n")
    (tests / "empty.py").write_text("# no tests\n")
    with PythonSandboxImage(repository, "v4-reliability-pytest-fixture") as build:
        yield build
