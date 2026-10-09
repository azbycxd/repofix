"""Explicit networked preparation and clean offline judging for custom tasks."""
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from repofix.env import DockerEnv
from repofix.python_sandbox import PythonSandboxImage
from .judge import run_test


@contextmanager
def task_environment(task, config=None):
    with tempfile.TemporaryDirectory(prefix="repofix-task-") as directory:
        repo = Path(directory) / "repository"
        subprocess.run(["git", "clone", "--quiet", "--", task.repo_url, str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "--detach", "--quiet", task.base_commit], check=True)
        run_id = "task-" + uuid.uuid4().hex[:12]
        with PythonSandboxImage(repo, run_id, setup_commands=task.setup_commands) as build:
            with DockerEnv(task.id, build.image, run_id,
                           sandbox_hardening=getattr(config, "sandbox_hardening", True),
                           sandbox_user=getattr(config, "sandbox_user", None)) as env:
                yield env


def docker_evaluator(checkout, setup_commands, nodes=None):
    run_id = "task-builder-" + uuid.uuid4().hex[:12]
    with PythonSandboxImage(checkout, run_id, setup_commands=setup_commands) as build:
        with DockerEnv("feature-builder", build.image, run_id, sandbox_hardening=True) as env:
            if nodes is None:
                collect = env.execute("python -m pytest --collect-only -q", timeout=600)
                if collect.exit_code != 0:
                    return {}
                nodes = [line.strip() for line in collect.output.splitlines() if "::" in line and " " not in line.strip()]
            return {node: run_test(env, node) for node in nodes}
