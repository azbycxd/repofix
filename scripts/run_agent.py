"""Run the single allowed RepoFix 1.2 DEV1 attempt."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
from datetime import datetime, timezone
from pathlib import Path

from datasets import load_dataset
from dotenv import load_dotenv
from swebench.harness.utils import make_test_spec


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from repofix.agent import AgentConfig, RepoFixAgent, parse_tool_arguments  # noqa: E402
from repofix.env import DockerEnv  # noqa: E402


DATASET = "SWE-bench/SWE-bench_Verified"
SPLIT = "test"
ALLOWED_INSTANCE = "django__django-16429"


def load_dev1() -> dict:
    dataset = load_dataset(DATASET, split=SPLIT)
    for row in dataset:
        if row["instance_id"] == ALLOWED_INSTANCE:
            return dict(row)
    raise RuntimeError(f"Missing allowed instance: {ALLOWED_INSTANCE}")


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("dev1-%Y%m%dT%H%M%SZ")


def check_tool_argument_parsing() -> None:
    valid, error = parse_tool_arguments('{"command": "pwd"}')
    assert valid == {"command": "pwd"} and error is None

    invalid_cases = ("{", "[1]", '"scalar"', "1", "true", "null")
    for arguments_text in invalid_cases:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", default=ALLOWED_INSTANCE)
    parser.add_argument("--run-id", default=make_run_id())
    parser.add_argument(
        "--offline-check",
        action="store_true",
        help="Validate dataset, credentials, image, and Docker without a provider call.",
    )
    args = parser.parse_args()
    if args.instance_id != ALLOWED_INSTANCE:
        parser.error(f"RepoFix 1.2 permits only {ALLOWED_INSTANCE}")

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    instance = load_dev1()
    spec = make_test_spec(instance)

    if args.offline_check:
        check_tool_argument_parsing()
        print("TOOL_ARGUMENT_OBJECT_CHECK=PASS")
        print("TOOL_ARGUMENT_INVALID_JSON_CHECK=PASS")
        print("TOOL_ARGUMENT_NON_OBJECT_CHECK=PASS")
        with DockerEnv(spec.instance_id, spec.image, f"{args.run_id}-check") as env:
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
            print("TESTBED_DJANGO_IMPORT_CHECK=PASS")
            checked_execute(env, "git status --short")
        print(f"DEEPSEEK_API_KEY_PRESENT={'YES' if api_key else 'NO'}")
        print("PROVIDER_CALLS=0")
        return 0

    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    run_dir = PROJECT_ROOT / "runs" / args.run_id
    trajectory_path = run_dir / f"{spec.instance_id}.jsonl"
    with DockerEnv(spec.instance_id, spec.image, args.run_id) as env:
        agent = RepoFixAgent(
            env=env,
            issue=instance["problem_statement"],
            trajectory_path=trajectory_path,
            api_key=api_key,
            config=AgentConfig(),
        )
        result = agent.run()

    run_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = run_dir / "prediction.json"
    prediction_path.write_text(
        json.dumps(
            [
                {
                    "instance_id": spec.instance_id,
                    "model_patch": result.patch,
                    "model_name_or_path": AgentConfig().model,
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "submitted": result.submitted,
                "steps": result.steps,
                "provider_calls": result.provider_calls,
                "tool_calls": result.tool_calls,
                "prompt_tokens": result.prompt_tokens,
                "cache_hit_tokens": result.cache_hit_tokens,
                "completion_tokens": result.completion_tokens,
                "max_estimated_cost_usd": result.max_estimated_cost_usd,
                "wall_time_seconds": result.wall_time_seconds,
                "patch_nonempty": bool(result.patch.strip()),
                "trajectory_path": str(trajectory_path.relative_to(PROJECT_ROOT)),
                "prediction_path": str(prediction_path.relative_to(PROJECT_ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
