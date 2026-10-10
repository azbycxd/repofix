"""Real direct Docker exec + real pytest/JUnit. No model calls."""

import json
import uuid

import pytest

from repofix.env import DockerEnv
from v4_helpers import call, make_runtime, v4_docker_image

pytestmark = pytest.mark.docker


@pytest.mark.parametrize(
    "target,expected,timeout",
    [
        ("tests/test_pass.py", "PASS", 30),
        ("tests/test_fail.py", "FAIL", 30),
        ("tests/test_skip.py", "INCONCLUSIVE", 30),
        ("tests/empty.py", "INCONCLUSIVE", 30),
        ("tests/test_timeout.py", "INCONCLUSIVE", 1),
    ],
)
def test_real_docker_evidence(
    tmp_path, v4_docker_image, target, expected, timeout, record_property
):
    with DockerEnv(
        "v4-val", v4_docker_image.image, uuid.uuid4().hex[:12], sandbox_hardening=True
    ) as env:
        runtime = make_runtime(tmp_path, env)
        result = call(runtime, "run_tests", {"targets": [target], "timeout": timeout})
        evidence = runtime.state.metadata["v4"]["validation_evidence"]
        assert evidence["outcome"] == expected, result.content
        assert (
            evidence["tests_passed"] == 1 if expected == "PASS" else evidence["outcome"] != "PASS"
        )
        env.container.reload()
        assert env.container.attrs["HostConfig"]["NetworkMode"] == "none"
        record_property("expected_validation", expected)
        record_property("observed_validation", evidence["outcome"])
        record_property("execution_backend", "real-Docker")
        record_property("failure_injection", expected != "PASS")


def test_real_pipeline_and_forged_text_cannot_verify(tmp_path, v4_docker_image, record_property):
    with DockerEnv("v4-val-pipe", v4_docker_image.image, uuid.uuid4().hex[:12]) as env:
        runtime = make_runtime(tmp_path, env)
        # Deliberately swallow failure; V4 pipefail is implemented separately in M4.
        result = call(
            runtime,
            "bash",
            {"command": "python -m pytest tests/test_fail.py | tee /tmp/v4-failure.log; true"},
        )
        assert result.exit_code == 0 and "failed" in result.content
        assert runtime.evidence_policy.current()["state"] == "UNVERIFIED"
        call(runtime, "bash", {"command": "echo 'pytest 100 passed'"})
        assert call(runtime, "submit").metadata["hook_blocked"]
        record_property("expected_validation", "UNVERIFIED")
        record_property("observed_validation", runtime.evidence_policy.current()["state"])
        record_property("failure_injection", True)
        record_property("execution_backend", "real-Docker")
