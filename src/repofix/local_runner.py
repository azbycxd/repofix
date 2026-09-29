"""Run RepoFix against a normal local Python Git repository."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .agent import AgentConfig, RepoFixAgent, TrajectoryWriter
from .env import DockerEnv
from .evaluation import build_evaluation_patch, changed_paths
from .python_sandbox import PythonSandboxImage
from .reproduction import is_test_path
from .worktree import LocalRepositoryError, TemporaryGitWorktree, inspect_repository


class LocalRunError(RuntimeError):
    """Raised for a clear, user-facing local CLI failure."""


@dataclass(frozen=True)
class LocalRunOutcome:
    summary: dict[str, Any]
    full_diff: str
    production_diff: str


def _implementation_commit() -> str:
    project = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _prepare_output(output_dir: Path, repository: Path) -> Path:
    output = output_dir.expanduser().resolve()
    try:
        output.relative_to(repository)
    except ValueError:
        pass
    else:
        raise LocalRunError(
            "output directory must be outside the source repository"
        )
    if output.exists() and any(output.iterdir()):
        raise LocalRunError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def run_local_repository(
    repository: Path,
    issue: str,
    output_dir: Path,
    api_key: str,
    run_id: str,
    config: AgentConfig | None = None,
) -> LocalRunOutcome:
    if not issue.strip():
        raise LocalRunError("issue description must not be empty")
    try:
        source_before = inspect_repository(repository)
    except LocalRepositoryError as exc:
        raise LocalRunError(str(exc)) from exc
    output = _prepare_output(output_dir, source_before.root)
    trajectory_path = output / "trajectory.jsonl"
    full_diff = ""
    production_diff = ""
    result = None
    build_seconds = 0.0
    install_commands: tuple[str, ...] = ()
    network_mode = "unknown"
    isolated_patch_materialized = False
    started = time.monotonic()
    error: str | None = None
    worktree = TemporaryGitWorktree(source_before.root)

    try:
        with worktree:
            assert worktree.path is not None
            with PythonSandboxImage(worktree.path, run_id) as build:
                build_seconds = build.build_seconds
                install_commands = build.install_commands
                with DockerEnv(
                    instance_id=source_before.root.name,
                    image=build.image,
                    run_id=run_id,
                ) as env:
                    assert env.container is not None
                    env.container.reload()
                    network_mode = env.container.attrs["HostConfig"]["NetworkMode"]
                    if network_mode != "none":
                        raise LocalRunError(
                            f"runtime container network is not disabled: {network_mode}"
                        )
                    result = RepoFixAgent(
                        env=env,
                        issue=issue,
                        trajectory_path=trajectory_path,
                        api_key=api_key,
                        git_commit=_implementation_commit(),
                        config=config or AgentConfig(),
                    ).run()
                    full_diff = result.patch
            worktree.materialize_patch(full_diff)
            isolated_patch_materialized = worktree.diff() == full_diff
            if full_diff and not isolated_patch_materialized:
                raise LocalRunError(
                    "container patch did not match the temporary worktree diff"
                )
    except Exception as exc:
        error = str(exc).replace(api_key, "[REDACTED]")
        if not trajectory_path.exists():
            TrajectoryWriter(trajectory_path, secrets=[api_key]).write(
                {"type": "local_run_error", "error": error}
            )

    source_after = inspect_repository(source_before.root)
    source_unchanged = (
        source_before.head == source_after.head
        and source_before.status == source_after.status
    )
    evaluation = build_evaluation_patch(full_diff)
    production_diff = evaluation.patch
    all_paths = changed_paths(full_diff)
    production_paths = [path for path in all_paths if not is_test_path(path)]
    test_paths = [path for path in all_paths if is_test_path(path)]

    (output / "full.patch").write_text(full_diff, encoding="utf-8")
    (output / "production.patch").write_text(production_diff, encoding="utf-8")
    summary: dict[str, Any] = {
        "run_id": run_id,
        "repository": str(source_before.root),
        "source_head_before": source_before.head,
        "source_head_after": source_after.head,
        "source_status_before": source_before.status,
        "source_status_after": source_after.status,
        "source_repository_unchanged": source_unchanged,
        "isolated_patch_materialized": isolated_patch_materialized,
        "runtime_network_mode": network_mode,
        "sandbox_build_seconds": build_seconds,
        "sandbox_install_commands": list(install_commands),
        "terminal_status": result.status if result is not None else "error",
        "submitted": result.submitted if result is not None else False,
        "steps": result.steps if result is not None else 0,
        "provider_calls": result.provider_calls if result is not None else 0,
        "tool_calls": result.tool_calls if result is not None else 0,
        "prompt_tokens": result.prompt_tokens if result is not None else 0,
        "cache_hit_tokens": result.cache_hit_tokens if result is not None else None,
        "completion_tokens": result.completion_tokens if result is not None else 0,
        "estimated_cost_usd": (
            result.max_estimated_cost_usd if result is not None else 0.0
        ),
        "pre_fix_reproduction_telemetry": (
            result.pre_fix_reproduced if result is not None else False
        ),
        "reproduction_flipped_telemetry": (
            result.repro_flipped if result is not None else False
        ),
        "modified_production_files": production_paths,
        "modified_test_files": test_paths,
        "filtered_test_paths": list(evaluation.filtered_test_paths),
        "search_calls": result.search_calls if result is not None else 0,
        "reviewer_enabled": (
            (config or AgentConfig()).reviewer_enabled
        ),
        "reviewer_calls": result.reviewer_calls if result is not None else 0,
        "dense_loaded": any(
            name == "fastembed" or name.startswith("fastembed.")
            for name in sys.modules
        ),
        "full_diff_path": str(output / "full.patch"),
        "production_diff_path": str(output / "production.patch"),
        "trajectory_path": str(trajectory_path),
        "wall_time_seconds": time.monotonic() - started,
        "error": error,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not source_unchanged:
        raise LocalRunError("source repository changed during RepoFix run")
    return LocalRunOutcome(summary, full_diff, production_diff)
