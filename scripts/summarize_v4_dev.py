"""Read-only analysis after live attempts; no model calls, no reruns, no scoring edits."""

import json
import shutil
import statistics
from collections import Counter

from run_v4_dev import DEV
from v4_evidence import OUT, ROOT

from repofix.harness.evidence import file_hash


def total(rows, name):
    values = [row.get(name) for row in rows]
    return sum(values) if values and all(value is not None for value in values) else None


def aggregate(rows, name):
    values = [row[name] for row in rows if row.get(name) is not None]
    return {
        "measured": len(values),
        "sum": sum(values) if values else None,
        "median": statistics.median(values) if values else None,
        "max": max(values) if values else None,
    }


def main():
    folder = OUT / "dev-final"
    target = folder / "analysis.json"
    if target.exists():
        raise SystemExit("Refusing to overwrite existing analysis")
    execution = json.loads((folder / "execution.json").read_text())
    summaries = json.loads((folder / "agent-results.json").read_text())
    official = []
    for path in (folder / "judge").glob("*.json"):
        data = json.loads(path.read_text())
        if "resolved_ids" in data:
            official.append((path, data))
    if len(official) > 1:
        raise RuntimeError("Ambiguous official aggregate report")
    report = official[0][1] if official else {}
    raw = folder / "judge-raw"
    raw.mkdir(exist_ok=False)
    logs = ROOT / "logs/run_evaluation" / (folder.name + "-official")
    # Copy only logs of the three explicit DEV instances. Never enumerate or
    # inspect any other experiment/instance's source or evaluation patch.
    for instance in DEV:
        source = logs / "deepseek-flash" / instance
        if source.is_dir():
            for path in source.iterdir():
                if path.is_file() and path.name in {
                    "report.json",
                    "test_output.txt",
                    "run_instance.log",
                }:
                    destination = raw / instance / path.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
    rows = []
    for result in summaries:
        instance = result["instance_id"]
        assert instance in DEV
        path = folder / "traces" / instance / "trajectory.jsonl"
        trace = [json.loads(line) for line in path.read_text().splitlines()]
        steps = [r for r in trace if r.get("type") == "step"]
        tools = [r for r in trace if r.get("type") == "tool_execution"]
        checkpoints = [r for r in trace if r.get("type") == "checkpoint"]
        progress = [r for r in trace if r.get("type") == "progress"]
        validation = [r["evidence"] for r in trace if r.get("type") == "validation"]
        verdict = "NOT_JUDGED"
        if instance in report.get("resolved_ids", []):
            verdict = "RESOLVED"
        elif instance in report.get("unresolved_ids", []):
            verdict = "UNRESOLVED"
        elif instance in report.get("error_ids", []):
            verdict = "JUDGE_ERROR"
        reliability = result.get("reliability", {})
        rows.append(
            {
                **result,
                "judge_verdict": verdict,
                "judge_empty_patch": instance in report.get("empty_patch_ids", []),
                "local_validation": reliability.get("local_validation"),
                "estimated_total_usd": reliability.get("estimated_total_usd"),
                "model_time": aggregate(steps, "model_latency_seconds"),
                "tool_time": aggregate(tools, "duration_seconds"),
                "checkpoint_time": aggregate(checkpoints, "checkpoint_duration"),
                "snapshot_bytes": aggregate(checkpoints, "snapshot_bytes"),
                "progress_time": aggregate(progress, "duration_seconds"),
                "progress_actions": dict(Counter(r["action"] for r in progress if r.get("action"))),
                "tool_names": dict(Counter(r["tool_name"] for r in tools)),
                "validation_outcomes": dict(Counter(r["outcome"] for r in validation)),
                "validation_errors": sorted(
                    {r["parse_error"] for r in validation if r.get("parse_error")}
                ),
                "runtime_errors": [
                    r for r in trace if r.get("type") in {"runtime_error", "runner_error"}
                ],
                "trajectory_sha256": file_hash(path),
            }
        )
    values = {
        name: total(rows, name)
        for name in (
            "steps",
            "provider_calls",
            "tool_calls",
            "prompt_tokens",
            "completion_tokens",
            "cache_hit_tokens",
            "max_estimated_cost_usd",
            "estimated_total_usd",
            "wall_time_seconds",
        )
    }
    values["total_tokens"] = (
        values["prompt_tokens"] + values["completion_tokens"]
        if values["prompt_tokens"] is not None and values["completion_tokens"] is not None
        else None
    )
    analysis = {
        "tasks": rows,
        "totals": values,
        "execution": execution,
        "official_report": {
            "path": str(official[0][0].relative_to(ROOT)),
            "sha256": file_hash(official[0][0]),
            "data": report,
        }
        if official
        else None,
        "resolved": sum(r["judge_verdict"] == "RESOLVED" for r in rows),
        "judged": sum(r["judge_verdict"] in {"RESOLVED", "UNRESOLVED"} for r in rows),
        "not_attempted": [task for task in DEV if task not in {r["instance_id"] for r in rows}],
        "note": "Three known DEV regressions, one attempt each; not a generalization benchmark.",
        "holdout_agent_runs": 0,
    }
    target.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"resolved": analysis["resolved"], "judged": analysis["judged"], "totals": values},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
