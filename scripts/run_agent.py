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

from repofix.cli import main as cli_main
from repofix.harness.config import HarnessConfig

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
from repofix.evaluation import build_evaluation_patch  # noqa: E402
from repofix.search import BM25Index, format_search_results  # noqa: E402

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


def check_native_file_tools(env: DockerEnv) -> None:
    path = "/testbed/.repofix_tool_check.py"
    created = env.execute(f"printf '%s\\n' 'value = 1' 'marker = \"one\"' > {path}")
    assert not created.timed_out and created.exit_code == 0

    viewed = env.view_file(path, 1, 2)
    assert "     1\tvalue = 1" in viewed
    assert '     2\tmarker = "one"' in viewed
    try:
        env.view_file(path, 3, 4)
    except ValueError as exc:
        assert "exceeds file length" in str(exc)
    else:
        raise AssertionError("view accepted a range beyond the file")
    try:
        env.view_file("/etc/passwd", 1, 1)
    except ValueError as exc:
        assert "inside /testbed" in str(exc)
    else:
        raise AssertionError("view accepted a path outside /testbed")

    original = env.view_file(path, 1, 2)
    missing = env.str_replace_file(path, "missing", "replacement")
    assert not missing.success and not missing.syntax_rollback
    assert env.view_file(path, 1, 2) == original
    multiple = env.str_replace_file(path, " = ", " == ")
    assert not multiple.success and not multiple.syntax_rollback
    assert env.view_file(path, 1, 2) == original

    success = env.str_replace_file(path, "value = 1", "value = 2")
    assert success.success and not success.syntax_rollback
    assert "py_compile passed" in success.output
    assert "value = 2" in env.view_file(path, 1, 2)

    rollback = env.str_replace_file(path, "value = 2", "value =")
    assert not rollback.success and rollback.syntax_rollback
    assert "original file restored" in rollback.output
    assert "value = 2" in env.view_file(path, 1, 2)

    text_path = "/testbed/.repofix_tool_check.txt"
    text_created = env.execute(f"printf 'plain text\\n' > {text_path}")
    assert not text_created.timed_out and text_created.exit_code == 0
    text_edit = env.str_replace_file(text_path, "plain text", "not python: [")
    assert text_edit.success and not text_edit.syntax_rollback
    assert "Syntax check skipped for non-Python file" in text_edit.output
    assert "not python: [" in env.view_file(text_path, 1, 1)
    print("VIEW_LINE_RANGE_CHECK=PASS")
    print("VIEW_PATH_GUARD_CHECK=PASS")
    print("STR_REPLACE_CARDINALITY_CHECK=PASS")
    print("STR_REPLACE_SUCCESS_CHECK=PASS")
    print("STR_REPLACE_SYNTAX_ROLLBACK_CHECK=PASS")
    print("STR_REPLACE_NON_PYTHON_CHECK=PASS")


def check_code_search(env: DockerEnv) -> None:
    index, stats = BM25Index.from_repository(env)
    assert stats.file_count > 0
    assert stats.chunk_count > 0
    results = index.search("django model field", top_k=5)
    assert results
    rendered = format_search_results(results)
    assert ":" in rendered
    assert "type=" in rendered
    assert "score=" in rendered
    assert "--- source ---" not in rendered
    config = AgentConfig()
    search_view = format_tool_observation(
        rendered,
        "",
        None,
        config.tool_output_max_chars,
        config.tool_output_head_chars,
        config.tool_output_tail_chars,
    )
    assert not search_view.truncated
    print(f"BM25_INDEX_FILES={stats.file_count}")
    print(f"BM25_INDEX_CHUNKS={stats.chunk_count}")
    print(f"BM25_INDEX_BUILD_SECONDS={stats.build_seconds:.6f}")
    print("BM25_SEARCH_POSITION_ONLY=PASS")
    print("BM25_SEARCH_NO_TRUNCATION=PASS")
    print("BM25_SEARCH_CHECK=PASS")


def check_patch_collection(env: DockerEnv) -> None:
    created = env.execute(
        "printf 'temporary untracked\\n' > .repofix_untracked_check.txt; "
        "printf 'intentional staged\\n' > .repofix_staged_check.txt; "
        "git add .repofix_staged_check.txt"
    )
    assert not created.timed_out and created.exit_code == 0
    changes = env.get_tracked_changes()
    assert ("A", ".repofix_staged_check.txt") in changes
    patch = env.get_diff()
    assert ".repofix_untracked_check.txt" not in patch
    assert ".repofix_staged_check.txt" in patch
    cleanup = env.execute(
        "git reset -- .repofix_staged_check.txt; "
        "rm -f .repofix_untracked_check.txt .repofix_staged_check.txt"
    )
    assert not cleanup.timed_out and cleanup.exit_code == 0
    print("PATCH_EXCLUDES_UNTRACKED=PASS")
    print("PATCH_INCLUDES_STAGED_NEW_FILE=PASS")
    print("TRACKED_CHANGE_TELEMETRY_CHECK=PASS")


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
                check_native_file_tools(env)
                check_code_search(env)
                check_patch_collection(env)
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
        "view_calls": result.view_calls,
        "str_replace_calls": result.str_replace_calls,
        "str_replace_failures": result.str_replace_failures,
        "syntax_rollbacks": result.syntax_rollbacks,
        "search_calls": result.search_calls,
        "truncations": result.truncations,
        "index_file_count": result.index_file_count,
        "index_chunk_count": result.index_chunk_count,
        "index_build_seconds": result.index_build_seconds,
        "dense_build_seconds": result.dense_build_seconds,
        "dense_cache_hit": result.dense_cache_hit,
        "dense_cache_hit_count": result.dense_cache_hit_count,
        "dense_embedded_count": result.dense_embedded_count,
        "PRE_FIX_REPRODUCED": result.pre_fix_reproduced,
        "POST_FIX_REPRO_PASSED": result.post_fix_repro_passed,
        "REPRO_FLIPPED": result.repro_flipped,
        "FIRST_PRODUCTION_EDIT_STEP": result.first_production_edit_step,
        "EXISTING_TEST_MODIFIED": result.existing_test_modified,
        "GIT_HISTORY_SEARCH_COUNT": result.git_history_search_count,
        "NETWORK_ATTEMPT_COUNT": result.network_attempt_count,
        "reviewer_verdict": result.reviewer_verdict,
        "reviewer_reject_reason": result.reviewer_reject_reason,
        "reviewer_returned": result.reviewer_returned,
        "patch_changed_after_reject": result.patch_changed_after_reject,
        "reviewer_calls": result.reviewer_calls,
        "reviewer_prompt_tokens": result.reviewer_prompt_tokens,
        "reviewer_cache_hit_tokens": result.reviewer_cache_hit_tokens,
        "reviewer_completion_tokens": result.reviewer_completion_tokens,
        "reviewer_cost_usd": result.reviewer_cost_usd,
        "reviewer_latency_seconds": result.reviewer_latency_seconds,
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
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    spec = make_test_spec(instance)
    instance_id = spec.instance_id
    trajectory_path = trajectory_dir / f"{instance_id}.jsonl"
    if getattr(config, "profile", "v1") == "v3":
        trajectory_path = trajectory_dir / instance_id / "trajectory.jsonl"
    patch = ""
    reviewer_initial_patch = ""
    try:
        with DockerEnv(
            instance_id,
            spec.image,
            run_id,
            sandbox_hardening=getattr(config, "sandbox_hardening", False),
            sandbox_user=getattr(config, "sandbox_user", None),
        ) as env:
            result = RepoFixAgent(
                env=env,
                issue=instance["problem_statement"],
                trajectory_path=trajectory_path,
                api_key=api_key,
                git_commit=git_commit,
                config=config,
            ).run()
        patch = result.patch
        reviewer_initial_patch = result.reviewer_initial_patch
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
            "view_calls": 0,
            "str_replace_calls": 0,
            "str_replace_failures": 0,
            "syntax_rollbacks": 0,
            "search_calls": 0,
            "truncations": 0,
            "index_file_count": 0,
            "index_chunk_count": 0,
            "index_build_seconds": 0.0,
            "dense_build_seconds": 0.0,
            "dense_cache_hit": False,
            "dense_cache_hit_count": 0,
            "dense_embedded_count": 0,
            "PRE_FIX_REPRODUCED": False,
            "POST_FIX_REPRO_PASSED": False,
            "REPRO_FLIPPED": False,
            "FIRST_PRODUCTION_EDIT_STEP": None,
            "EXISTING_TEST_MODIFIED": False,
            "GIT_HISTORY_SEARCH_COUNT": 0,
            "NETWORK_ATTEMPT_COUNT": 0,
            "reviewer_verdict": None,
            "reviewer_reject_reason": None,
            "reviewer_returned": False,
            "patch_changed_after_reject": False,
            "reviewer_calls": 0,
            "reviewer_prompt_tokens": 0,
            "reviewer_cache_hit_tokens": None,
            "reviewer_completion_tokens": 0,
            "reviewer_cost_usd": 0.0,
            "reviewer_latency_seconds": 0.0,
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
    evaluation = build_evaluation_patch(patch)
    evaluation_patch_path = run_dir / f"{instance_id}.evaluation.patch"
    evaluation_patch_path.write_text(evaluation.patch, encoding="utf-8")
    summary["patch_path"] = str(patch_path.relative_to(PROJECT_ROOT))
    summary["evaluation_patch_path"] = str(evaluation_patch_path.relative_to(PROJECT_ROOT))
    summary["evaluation_patch_nonempty"] = bool(evaluation.patch.strip())
    summary["filtered_test_paths"] = list(evaluation.filtered_test_paths)
    result_path = run_dir / f"{instance_id}.result.json"
    result_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    prediction = {
        "instance_id": instance_id,
        "model_patch": evaluation.patch,
        "model_name_or_path": config.model,
    }
    reviewer_prediction = None
    if summary["reviewer_verdict"] == "REJECT":
        reviewer_patch_path = run_dir / f"{instance_id}.reviewer-initial.patch"
        reviewer_patch_path.write_text(reviewer_initial_patch, encoding="utf-8")
        reviewer_evaluation = build_evaluation_patch(reviewer_initial_patch)
        reviewer_evaluation_path = run_dir / f"{instance_id}.reviewer-initial.evaluation.patch"
        reviewer_evaluation_path.write_text(reviewer_evaluation.patch, encoding="utf-8")
        summary["reviewer_initial_patch_path"] = str(reviewer_patch_path.relative_to(PROJECT_ROOT))
        summary["reviewer_initial_evaluation_patch_path"] = str(
            reviewer_evaluation_path.relative_to(PROJECT_ROOT)
        )
        summary["reviewer_initial_filtered_test_paths"] = list(
            reviewer_evaluation.filtered_test_paths
        )
        reviewer_prediction = {
            "instance_id": instance_id,
            "model_patch": reviewer_evaluation.patch,
            "model_name_or_path": config.model,
        }
        result_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary, prediction, reviewer_prediction


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("v1", "v3"), default="v1")
    parser.add_argument("--resume", type=Path)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--instance-id")
    selection.add_argument("--all-dev", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--output-dir",
        help="Experiment output directory; defaults to runs/<run-id>.",
    )
    parser.add_argument(
        "--reviewer",
        action="store_true",
        help="Enable one independent review on the first submit call.",
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
    parser.add_argument(
        "--retrieval-mode",
        choices=("bm25", "dense_rrf"),
        default="bm25",
        help="Retrieval backend; dense_rrf requires requirements-dense.txt.",
    )
    args = parser.parse_args()

    if args.resume:
        return cli_main(["resume", str(args.resume)])

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
    run_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "runs" / run_id
    if not run_dir.is_absolute():
        run_dir = PROJECT_ROOT / run_dir
    run_dir = run_dir.resolve()
    trajectory_dir = Path(args.trajectory_dir) if args.trajectory_dir else run_dir
    if not trajectory_dir.is_absolute():
        trajectory_dir = PROJECT_ROOT / trajectory_dir
    trajectory_dir = trajectory_dir.resolve()
    for path in (run_dir, trajectory_dir):
        if path != PROJECT_ROOT and PROJECT_ROOT not in path.parents:
            raise RuntimeError("Experiment output must remain inside the project")
    if run_dir.exists() and any(run_dir.iterdir()):
        raise RuntimeError(f"Run directory is not empty: {run_dir}")
    if trajectory_dir != run_dir and trajectory_dir.exists() and any(trajectory_dir.iterdir()):
        raise RuntimeError(f"Trajectory directory is not empty: {trajectory_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    trajectory_dir.mkdir(parents=True, exist_ok=True)

    git_commit = current_git_commit()
    config = HarnessConfig.for_profile(
        args.profile,
        retrieval_mode=args.retrieval_mode,
        reviewer_enabled=args.reviewer,
    )
    summaries: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    reviewer_initial_predictions: list[dict[str, Any]] = []
    for instance_id in selected_ids:
        summary, prediction, reviewer_prediction = run_instance(
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
        if reviewer_prediction is not None:
            reviewer_initial_predictions.append(reviewer_prediction)
        print(json.dumps(summary, ensure_ascii=False))

    predictions_path = run_dir / "predictions.json"
    predictions_path.write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    reviewer_predictions_path = run_dir / "reviewer_initial_predictions.json"
    reviewer_predictions_path.write_text(
        json.dumps(reviewer_initial_predictions, ensure_ascii=False, indent=2) + "\n",
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
        "reviewer_initial_predictions_path": str(
            reviewer_predictions_path.relative_to(PROJECT_ROOT)
        ),
    }
    (run_dir / "batch_results.json").write_text(
        json.dumps(batch_result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(batch_result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
