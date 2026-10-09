"""Evaluate BM25 file localization on the five fixed DEV tasks only."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from datasets import load_dataset
from swebench.harness.utils import make_test_spec

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from repofix.env import DockerEnv  # noqa: E402
from repofix.search import BM25Index  # noqa: E402

DATASET = "SWE-bench/SWE-bench_Verified"
SPLIT = "test"
TASKS_PATH = PROJECT_ROOT / "tasks.txt"
HOLDOUT_PATH = PROJECT_ROOT / "holdout.txt"
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+) b/(.+)$", re.MULTILINE)


def read_ids(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_dev_instances(dev_ids: list[str]) -> dict[str, dict[str, Any]]:
    wanted = set(dev_ids)
    found: dict[str, dict[str, Any]] = {}
    for row in load_dataset(DATASET, split=SPLIT):
        if row["instance_id"] in wanted:
            found[row["instance_id"]] = dict(row)
    if set(found) != wanted:
        raise RuntimeError(f"Missing DEV instances: {sorted(wanted - set(found))}")
    return found


def gold_files_from_patch(patch: str) -> list[str]:
    files = []
    for _, destination in _DIFF_HEADER_RE.findall(patch):
        if destination != "/dev/null" and destination not in files:
            files.append(destination)
    if len(files) != 1:
        raise RuntimeError(f"Expected one gold file, found {len(files)}")
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="step-2-3-bm25-offline")
    args = parser.parse_args()

    dev_ids = read_ids(TASKS_PATH)
    holdout_ids = read_ids(HOLDOUT_PATH)
    if set(dev_ids) & set(holdout_ids):
        raise RuntimeError("DEV and sealed HOLDOUT lists overlap")
    instances = load_dev_instances(dev_ids)

    rows: list[dict[str, Any]] = []
    for instance_id in dev_ids:
        instance = instances[instance_id]
        gold_file = gold_files_from_patch(instance["patch"])[0]
        spec = make_test_spec(instance)
        with DockerEnv(instance_id, spec.image, args.run_id) as env:
            index, stats = BM25Index.from_repository(env)
            ranked = index.search(instance["problem_statement"], top_k=max(1, len(index.chunks)))
        rank = next(
            (
                position
                for position, result in enumerate(ranked, start=1)
                if result.chunk.file_path == gold_file
            ),
            None,
        )
        row = {
            "instance_id": instance_id,
            "gold_file": gold_file,
            "rank": rank,
            "top1": rank is not None and rank <= 1,
            "top3": rank is not None and rank <= 3,
            "top5": rank is not None and rank <= 5,
            "index_file_count": stats.file_count,
            "index_chunk_count": stats.chunk_count,
            "build_seconds": stats.build_seconds,
            "top5_files": [result.chunk.file_path for result in ranked[:5]],
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    report = {
        "dataset": DATASET,
        "split": SPLIT,
        "tasks": rows,
        "recall_at_1": sum(row["top1"] for row in rows) / len(rows),
        "recall_at_3": sum(row["top3"] for row in rows) / len(rows),
        "recall_at_5": sum(row["top5"] for row in rows) / len(rows),
        "total_build_seconds": sum(row["build_seconds"] for row in rows),
        "holdout_agent_runs": 0,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
