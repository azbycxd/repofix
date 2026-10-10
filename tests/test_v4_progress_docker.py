"""A genuinely running container job must be waiting, not ordinary repeated failure."""

import json
import uuid

import pytest

from repofix.env import DockerEnv
from repofix.harness.progress import ProgressMonitor
from v4_helpers import call, make_runtime, v4_docker_image

pytestmark = pytest.mark.docker


def test_real_job_wait_does_not_trigger_stuck(tmp_path, v4_docker_image, record_property):
    with DockerEnv("v4-progress", v4_docker_image.image, uuid.uuid4().hex[:12]) as env:
        runtime = make_runtime(tmp_path, env, progress_no_progress_steps=1)
        monitor = ProgressMonitor(runtime.config, env, runtime.state)
        result = call(runtime, "bash", {"command": "sleep 15", "run_in_background": True})
        job = result.metadata["job_id"]
        try:
            for step in range(1, 4):
                runtime.state.step = step
                result = call(runtime, "job_output", {"job_id": job})
                sample = monitor.observe(
                    [{"name": "job_output", "arguments": json.dumps({"job_id": job})}],
                    [{"content": result.content, **result.metadata}],
                )
                runtime.agent.trace.write(sample)
                assert sample["signal"] == "WAITING_FOR_JOB" and sample["action"] is None
            assert not runtime.state.termination
        finally:
            assert not runtime.jobs.kill(job)["still_running"]
        record_property("stuck_expected", False)
        record_property("stuck_observed", runtime.state.termination == "stuck")
        record_property("execution_backend", "real-Docker")
