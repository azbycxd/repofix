"""Run one DEV task or a frozen five-task RepoFix DEV experiment."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datasets import load_dataset
from dotenv import load_dotenv
from swebench.harness.utils import make_test_spec


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from repofix.agent import (  # noqa: E402
    AgentConfig,
    RepoFixAgent,
    TrajectoryWriter,
    format_tool_observation,
    parse_tool_arguments,
)
from repofix.env import DockerEnv  # noqa: E402


DATASET = "SWE-bench/SWE-bench_Verified"
SPLIT = "test"
TASKS_PATH = PROJECT_ROOT / "tasks.txt"
HOLDOUT_PATH = PROJECT_ROOT / "holdout.txt"


def read_instance_ids(path: Path) -> list[str]:
    ids = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    ids = [instance_id for instance_id in ids if instance_id]
    if not ids or len(ids) != len(set(ids)):
        raise RuntimeError(f"Invalid instance list: {path.name}")
    return ids


def validate_selected_ids(
    selected_ids: list[str], dev_ids: list[str], holdout_ids: list[str]
) -> None:
    holdout_overlap = set(selected_ids) & set(holdout_ids)
    if holdout_overlap:
        raise RuntimeError("Refusing to run a sealed HOLDOUT instance")
    unknown = set(selected_ids) - set(dev_ids)
    if unknown:
        raise RuntimeError("RepoFix 1.3 permits only IDs listed in tasks.txt")


def load_instances(selected_ids: list[str]) -> dict[str, dict[str, Any]]:
    wanted = set(selected_ids)
    found: dict[str, dict[str, Any]] = {}
    for row in load_dataset(DATASET, split=SPLIT):
        instance_id = row["instance_id"]
        if instance_id in wanted:
            found[instance_id] = dict(row)
    missing = wanted - set(found)
    if missing:
        raise RuntimeError(f"Missing selected DEV instances: {sorted(missing)}")
    return found


def current_git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()


def make_run_id(all_dev: bool) -> str:
    prefix = "dev-baseline" if all_dev else "dev"
    return datetime.now(timezone.utc).strftime(f"{prefix}-%Y%m%dT%H%M%SZ")


def check_tool_argument_parsing() -> None:
    valid, error = parse_tool_arguments('{"command": "pwd"}')
    assert valid == {"command": "pwd"} and error is None
    for arguments_text in ("{", "[1]", '"scalar"', "1", "true", "null"):
        parsed, error = parse_tool_arguments(arguments_text)
        assert parsed is None
        assert error is not None and error.startswith("Invalid tool arguments:")


def checked_execute(env: DockerEnv, command: str):
    result = env.execute(command)
    if result.timed_out or result.exit_code != 0:
        raise RuntimeError(
            f"Offline check failed for {command!r}: "
            f"exit_code={result.exit_code}, timed_out={result.timed_out}"
        )
    print(result.output, end="" if result.output.endswith("\n") else "\n")
    print(f"[exit_code={result.exit_code} timed_out={result.timed_out}]")
    return result


def check_tool_output_truncation(env: DockerEnv) -> None:
    config = AgentConfig()
    suffix = "\n[exit_code=0]"
    small = "short output\n"
    small_view = format_tool_observation(
        small,
        suffix,
        None,
        config.tool_output_max_chars,
        config.tool_output_head_chars,
        config.tool_output_tail_chars,
    )
    assert not small_view.truncated
    assert small_view.content == small + suffix
    assert small_view.returned_chars == len(small + suffix)

    large = (
        "HEAD_SENTINEL\n"
        + "".join(f"line-{index:04d}-{'x' * 24}\n" for index in range(1_000))
        + "TAIL_SENTINEL\n"
    )
    output_path = "/tmp/repofix_out_offline_check.txt"
    env.write_text_file(output_path, large)
    large_view = format_tool_observation(
        large,
        suffix,
        output_path,
        config.tool_output_max_chars,
        config.tool_output_head_chars,
        config.tool_output_tail_chars,
    )
    assert large_view.truncated
    assert large_view.original_chars == len(large)
    assert large_view.original_lines == len(large.splitlines())
    assert large[: config.tool_output_head_chars] in large_view.content
    assert large[-config.tool_output_tail_chars :] in large_view.content
    assert output_path in large_view.content
    assert large_view.returned_chars == len(large_view.content)

    restored = env.execute(f"cat {output_path}")
    assert not restored.timed_out and restored.exit_code == 0
    assert restored.output == large
    print("TOOL_OUTPUT_SMALL_UNCHANGED=PASS")
    print("TOOL_OUTPUT_LARGE_TRUNCATED=PASS")
    print("TOOL_OUTPUT_HEAD_TAIL=PASS")
    print("TOOL_OUTPUT_FULL_FILE_READBACK=PASS")


def offline_check(
    selected_ids: list[str],
    instances: dict[str, dict[str, Any]],
    api_key_present: bool,
    run_id: str,
) -> None:
    check_tool_argument_parsing()
    print("TOOL_ARGUMENT_CHECKS=PASS")
    for index, instance_id in enumerate(selected_ids):
        spec = make_test_spec(instances[instance_id])
        print(f"OFFLINE_INSTANCE={instance_id}")
        with DockerEnv(spec.instance_id, spec.image, f"{run_id}-check") as env:
            checked_execute(env, "pwd")
            checked_execute(env, "which python")
            checked_execute(env, "python --version")
            django_result = checked_execute(
                env, 'python -c "import django; print(django.__file__)"'
            )
            django_path = django_result.output.strip()
            if posixpath.commonpath([DockerEnv.workdir, django_path]) != DockerEnv.workdir:
                raise RuntimeError(
                    f"django was not imported from {DockerEnv.workdir}: {django_path}"
                )
            checked_execute(env, "git status --short")
            if index == 0:
                check_tool_output_truncation(env)
    print("TESTBED_DJANGO_IMPORT_CHECKS=PASS")
    print(f"DEEPSEEK_API_KEY_PRESENT={'YES' if api_key_present else 'NO'}")
    print("PROVIDER_CALLS=0")


def result_summary(instance_id: str, result, trajectory_path: Path) -> dict[str, Any]:
    return {
        "instance_id": instance_id,
        "status": result.status,
        "submitted": result.submitted,
        "steps": result.steps,
        "provider_calls": result.provider_calls,
        "tool_calls": result.tool_calls,
        "truncations": result.truncations,
        "prompt_tokens": result.prompt_tokens,
        "cache_hit_tokens": result.cache_hit_tokens,
        "completion_tokens": result.completion_tokens,
        "max_estimated_cost_usd": result.max_estimated_cost_usd,
        "wall_time_seconds": result.wall_time_seconds,
        "patch_nonempty": bool(result.patch.strip()),
        "trajectory_path": str(trajectory_path.relative_to(PROJECT_ROOT)),
    }


def run_instance(
    instance: dict[str, Any],
    run_dir: Path,
    trajectory_dir: Path,
    run_id: str,
    api_key: str,
    git_commit: str,
    config: AgentConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = make_test_spec(instance)
    instance_id = spec.instance_id
    trajectory_path = trajectory_dir / f"{instance_id}.jsonl"
    patch = ""
    try:
        with DockerEnv(instance_id, spec.image, run_id) as env:
            result = RepoFixAgent(
                env=env,
                issue=instance["problem_statement"],
                trajectory_path=trajectory_path,
                api_key=api_key,
                git_commit=git_commit,
                config=config,
            ).run()
        patch = result.patch
        summary = result_summary(instance_id, result, trajectory_path)
    except Exception as exc:
        safe_error = str(exc).replace(api_key, "[REDACTED]")
        trace = TrajectoryWriter(trajectory_path, secrets=[api_key])
        if not trajectory_path.exists():
            trace.write(
                {
                    "type": "config",
                    "step": 0,
                    "model": config.model,
                    "git_commit": git_commit,
                    "config": asdict(config),
                    "problem_statement": instance["problem_statement"],
                }
            )
        trace.write({"type": "runner_error", "error": safe_error})
        summary = {
            "instance_id": instance_id,
            "status": "runner_error",
            "submitted": False,
            "steps": 0,
            "provider_calls": 0,
            "tool_calls": 0,
            "truncations": 0,
            "prompt_tokens": 0,
            "cache_hit_tokens": None,
            "completion_tokens": 0,
            "max_estimated_cost_usd": 0.0,
            "wall_time_seconds": 0.0,
            "patch_nonempty": False,
            "trajectory_path": str(trajectory_path.relative_to(PROJECT_ROOT)),
            "error": safe_error,
        }
        trace.write({"type": "summary", **summary, "patch": ""})

    patch_path = run_dir / f"{instance_id}.patch"
    patch_path.write_text(patch, encoding="utf-8")
    summary["patch_path"] = str(patch_path.relative_to(PROJECT_ROOT))
    result_path = run_dir / f"{instance_id}.result.json"
    result_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    prediction = {
        "instance_id": instance_id,
        "model_patch": patch,
        "model_name_or_path": config.model,
    }
    return summary, prediction


def main() -> int:
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--instance-id")
    selection.add_argument("--all-dev", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--output-dir",
        help="Experiment output directory; defaults to runs/<run-id>.",
    )
    parser.add_argument(
        "--trajectory-dir",
        help="Optional single canonical directory for review trajectories.",
    )
    parser.add_argument(
        "--offline-check",
        action="store_true",
        help="Validate selected DEV containers without a Provider call.",
    )
    args = parser.parse_args()

    dev_ids = read_instance_ids(TASKS_PATH)
    holdout_ids = read_instance_ids(HOLDOUT_PATH)
    if set(dev_ids) & set(holdout_ids):
        raise RuntimeError("DEV and sealed HOLDOUT lists overlap")
    selected_ids = dev_ids if args.all_dev else [args.instance_id or dev_ids[0]]
    validate_selected_ids(selected_ids, dev_ids, holdout_ids)

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    instances = load_instances(selected_ids)
    run_id = args.run_id or make_run_id(args.all_dev)

    if args.offline_check:
        try:
            validate_selected_ids([holdout_ids[0]], dev_ids, holdout_ids)
        except RuntimeError:
            print("HOLDOUT_GUARD_CHECK=PASS")
        else:
            raise AssertionError("HOLDOUT guard did not reject a sealed instance")
        offline_check(selected_ids, instances, bool(api_key), run_id)
        return 0

    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    run_dir = (
        Path(args.output_dir)
        if args.output_dir
        else PROJECT_ROOT / "runs" / run_id
    )
    if not run_dir.is_absolute():
        run_dir = PROJECT_ROOT / run_dir
    run_dir = run_dir.resolve()
    trajectory_dir = (
        Path(args.trajectory_dir) if args.trajectory_dir else run_dir
    )
    if not trajectory_dir.is_absolute():
        trajectory_dir = PROJECT_ROOT / trajectory_dir
    trajectory_dir = trajectory_dir.resolve()
    for path in (run_dir, trajectory_dir):
        if path != PROJECT_ROOT and PROJECT_ROOT not in path.parents:
            raise RuntimeError("Experiment output must remain inside the project")
    if run_dir.exists() and any(run_dir.iterdir()):
        raise RuntimeError(f"Run directory is not empty: {run_dir}")
    if (
        trajectory_dir != run_dir
        and trajectory_dir.exists()
        and any(trajectory_dir.iterdir())
    ):
        raise RuntimeError(f"Trajectory directory is not empty: {trajectory_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    trajectory_dir.mkdir(parents=True, exist_ok=True)

    git_commit = current_git_commit()
    config = AgentConfig()
    summaries: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    for instance_id in selected_ids:
        summary, prediction = run_instance(
            instances[instance_id],
            run_dir,
            trajectory_dir,
            run_id,
            api_key,
            git_commit,
            config,
        )
        summaries.append(summary)
        predictions.append(prediction)
        print(json.dumps(summary, ensure_ascii=False))

    predictions_path = run_dir / "predictions.json"
    predictions_path.write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    batch_result = {
        "run_id": run_id,
        "git_commit": git_commit,
        "model": config.model,
        "instance_ids": selected_ids,
        "results": summaries,
        "trajectory_dir": str(trajectory_dir.relative_to(PROJECT_ROOT)),
        "predictions_path": str(predictions_path.relative_to(PROJECT_ROOT)),
    }
    (run_dir / "batch_results.json").write_text(
        json.dumps(batch_result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(batch_result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
