"""Exactly the three authorized DEV regressions, once, then independent official Judge.

Code is committed before --execute. No gold/test patch is loaded by the Agent
runner. Judge runs in a separate subprocess without Provider credentials.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from run_agent import (
    DATASET,
    SPLIT,
    load_instances,
    read_instance_ids,
    run_instance,
    validate_selected_ids,
)
from run_v4_reliability import ROOT, run_command, source_tree_hash
from v4_evidence import key
from repofix.harness.config import HarnessConfig, serialize_config

DEV = ["django__django-16429", "django__django-15277", "django__django-13343"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    validate_selected_ids(
        DEV, read_instance_ids(ROOT / "tasks.txt"), read_instance_ids(ROOT / "holdout.txt")
    )
    config = HarnessConfig.for_profile("v4")
    if not args.execute:
        print(json.dumps({"tasks": DEV, "config": serialize_config(config), "live_calls": 0}))
        return
    output = args.output.resolve()
    if ROOT not in output.parents:
        parser.error("output must be a new directory under this worktree")
    output.mkdir(parents=True, exist_ok=False)
    secret = key()
    if not secret:
        (output / "blockers.md").write_text("BLOCKED_PROVIDER: DeepSeek Key unavailable.\n")
        raise SystemExit(2)
    code = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    plan = {
        "code_sha": code,
        "source_tree_sha256": source_tree_hash(),
        "config": serialize_config(config),
        "dataset": DATASET,
        "split": SPLIT,
        "tasks": DEV,
        "max_estimated_total_usd": 1.0,
        "attempts_per_task": 1,
        "holdout_agent_runs": 0,
    }
    (output / "manifest.json").write_text(json.dumps(plan, indent=2) + "\n")
    instances = load_instances(DEV, public_only=True)
    summaries, predictions, blockers, cost = [], [], [], 0.0
    started = time.monotonic()
    for instance_id in DEV:
        # Default single-task cap remains $0.5. Leave margin for the final
        # response's post-hoc usage; do not silently change later tasks' config.
        if cost + config.max_cost_usd + 0.05 > 1.0:
            blockers.append(
                "BLOCKED_BUDGET: remaining estimate cannot cover unchanged task cap: " + instance_id
            )
            break
        summary, prediction, _ = run_instance(
            instances[instance_id],
            output,
            output / "traces",
            output.name + "-" + instance_id,
            secret,
            code,
            config,
        )
        summaries.append(summary)
        predictions.append(prediction)
        estimate = summary.get("reliability", {}).get("estimated_total_usd")
        (output / "agent-results.json").write_text(json.dumps(summaries, indent=2) + "\n")
        (output / "predictions.json").write_text(json.dumps(predictions, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "instance_id": instance_id,
                    "status": summary["status"],
                    "steps": summary["steps"],
                    "estimated_usd": estimate,
                }
            ),
            flush=True,
        )
        if estimate is None:
            blockers.append(
                "BLOCKED_COST_ACCOUNTING: provider/runtime usage incomplete; no further live requests."
            )
            break
        cost += estimate
    agent_seconds = time.monotonic() - started
    (output / "judge").mkdir()
    # Official help is read and preserved before choosing CLI arguments. This
    # driver uses the installed harness's documented options verified in M5.
    judge_command = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        DATASET,
        "--split",
        SPLIT,
        "--predictions_path",
        str(output / "predictions.json"),
        "--run_id",
        output.name + "-official",
        "--max_workers",
        "1",
        "--report_dir",
        str(output / "judge"),
        "--instance_ids",
        *[p["instance_id"] for p in predictions],
    ]
    judgement = (
        run_command(output, "official-judge", judge_command, timeout=1800) if predictions else None
    )
    (output / "blockers.md").write_text("\n".join(blockers) + "\n" if blockers else "none\n")
    (output / "execution.json").write_text(
        json.dumps(
            {
                "code_sha": code,
                "agent_wall_seconds": agent_seconds,
                "estimated_usd": cost,
                "estimate_complete": not blockers,
                "judge_command": judgement,
                "holdout_agent_runs": 0,
            },
            indent=2,
        )
        + "\n"
    )
    if blockers or judgement is None or judgement["exit_code"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
