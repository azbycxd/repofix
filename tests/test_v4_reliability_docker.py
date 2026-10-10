"""Local scope PASS can coexist with an independently failing hidden criterion."""

import json
import uuid
from dataclasses import asdict

import pytest

from repofix.env import DockerEnv
from repofix.tasks.judge import judge
from repofix.tasks.spec import TaskSpec
from v4_helpers import artifact_dir, call, make_runtime, v4_docker_image

pytestmark = pytest.mark.docker


def test_local_pass_is_not_independent_judge_resolution(tmp_path, v4_docker_image, record_property):
    folder = artifact_dir(tmp_path)
    with DockerEnv("v4-local-scope", v4_docker_image.image, uuid.uuid4().hex[:12]) as env:
        runtime = make_runtime(tmp_path, env)
        call(runtime, "run_tests", {"targets": ["tests/test_pass.py"]})
        assert not call(runtime, "submit").metadata.get("hook_blocked")
        assert runtime.evidence_policy.current()["state"] == "LOCAL_TESTS_PASSED"
        candidate = env.get_diff()
        assert not candidate
    hidden = (
        "diff --git a/tests/test_hidden.py b/tests/test_hidden.py\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/tests/test_hidden.py\n@@ -0,0 +1,2 @@\n"
        "+from module import VALUE\n+def test_required_behavior(): assert VALUE == 2\n"
    )
    task = TaskSpec(
        "local-scope-fixture",
        "bugfix",
        "trusted-self-authored-fixture",
        "0" * 40,
        "VALUE should be 2",
        hidden_test_patch=hidden,
        fail_to_pass=["tests/test_hidden.py::test_required_behavior"],
    )

    def factory(_):
        env = DockerEnv("v4-independent-judge", v4_docker_image.image, uuid.uuid4().hex[:12])
        env.v4_helpers = True
        return env

    result = judge(task, candidate, factory)
    assert result.error is None and result.resolved is False
    assert result.tests == {"tests/test_hidden.py::test_required_behavior": False}
    (folder / "independent-judge.json").write_text(json.dumps(asdict(result), indent=2))
    assert "test_hidden" not in runtime.agent.trace.path.read_text()
    record_property("expected_validation", "PASS")
    record_property("observed_validation", "PASS")
    record_property("judge_verdict", "UNRESOLVED")
    record_property("execution_backend", "real-Docker-independent-fixture-Judge-not-SWE-bench")
