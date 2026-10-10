"""Recreate an isolated environment, restore state, never replay side effects."""

import json
import uuid
from contextlib import ExitStack
from pathlib import Path

from repofix.agent import RepoFixAgent, TrajectoryWriter
from repofix.env import DockerEnv
from repofix.evaluation import build_evaluation_patch
from repofix.python_sandbox import PythonSandboxImage
from repofix.tasks.docker import task_environment
from repofix.tasks.spec import TaskSpec
from repofix.worktree import TemporaryGitWorktree, inspect_repository

from .checkpoint import CheckpointStore
from .checkpoint_v4 import checkpoint_store
from .config import HarnessConfig


def resume_run(run_dir, api_key):
    run_dir = Path(run_dir).resolve()
    manifest = json.loads((run_dir / "resume.json").read_text())
    config = HarnessConfig(**manifest["config"])
    audit = TrajectoryWriter(run_dir / "resume-audit.jsonl", secrets=[api_key])
    store = checkpoint_store(config, run_dir, audit._redact, audit.secrets)
    state, _ = store.load()
    CheckpointStore.require_resumable(state)
    if manifest.get("python_hooks_present"):
        raise ValueError("Python hooks must be re-registered via the programmatic resume API")
    holdout = Path(__file__).resolve().parents[3] / "holdout.txt"
    if manifest.get("instance_id") in set(holdout.read_text().split()):
        raise ValueError("Refusing to resume a HOLDOUT task")
    if config.profile not in {"v3", "v4"}:
        raise ValueError("resume requires a v3 or v4 checkpoint")
    name = Path(manifest["trajectory"])
    if name.name != str(name):
        raise ValueError("trajectory must be inside the run directory")
    run_id = "resume-" + uuid.uuid4().hex[:12]
    with ExitStack() as stack:
        env = None
        if manifest["kind"] == "task":
            task = TaskSpec(
                manifest["instance_id"],
                config.task_kind,
                manifest["repo_url"],
                manifest["base_commit"],
                manifest["issue"],
                setup_commands=manifest.get("setup_commands", []),
            )
            env = stack.enter_context(task_environment(task, config))
        elif manifest["kind"] == "local":
            repo = Path(manifest["repository"])
            if inspect_repository(repo).head != manifest["base_commit"]:
                raise ValueError(
                    "source HEAD changed since checkpoint; restore the original revision first"
                )
            worktree = stack.enter_context(TemporaryGitWorktree(repo))
            build = stack.enter_context(PythonSandboxImage(worktree.path, run_id))
            image, instance_id = build.image, repo.name
        else:
            image, instance_id = manifest["image"], manifest["instance_id"]
        if env is None:
            env = stack.enter_context(
                DockerEnv(
                    instance_id,
                    image,
                    run_id,
                    sandbox_hardening=config.sandbox_hardening,
                    sandbox_user=config.sandbox_user,
                )
            )
        state = store.resume(env)
        agent = RepoFixAgent(
            env, manifest["issue"], run_dir / name, api_key, manifest["git_commit"], config
        )
        agent.state, agent.resume_metadata = state, manifest
        result = agent.run()
        (run_dir / "full.patch").write_text(agent.trace._redact_text(result.patch))
        (run_dir / "production.patch").write_text(
            agent.trace._redact_text(build_evaluation_patch(result.patch).patch)
        )
        return result
