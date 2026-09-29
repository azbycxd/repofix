"""Compare BM25, dense, and RRF localization on the five fixed DEV tasks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from datasets import load_dataset
from swebench.harness.utils import make_test_spec


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from repofix.env import DockerEnv  # noqa: E402
from repofix.retrieval import HybridCodeIndex, short_query_proxy  # noqa: E402


DATASET = "SWE-bench/SWE-bench_Verified"
SPLIT = "test"
TASKS_PATH = PROJECT_ROOT / "tasks.txt"
HOLDOUT_PATH = PROJECT_ROOT / "holdout.txt"
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+) b/(.+)$", re.MULTILINE)


def read_ids(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_dev_instances(dev_ids: list[str]) -> dict[str, dict[str, Any]]:
    wanted = set(dev_ids)
    found: dict[str, dict[str, Any]] = {}
    for row in load_dataset(DATASET, split=SPLIT):
        if row["instance_id"] in wanted:
            found[row["instance_id"]] = dict(row)
    if set(found) != wanted:
        raise RuntimeError(f"Missing DEV instances: {sorted(wanted - set(found))}")
    return found


def gold_file_from_patch(patch: str) -> str:
    files: list[str] = []
    for _, destination in _DIFF_HEADER_RE.findall(patch):
        if destination != "/dev/null" and destination not in files:
            files.append(destination)
    if len(files) != 1:
        raise RuntimeError(f"Expected one gold file, found {len(files)}")
    return files[0]


def file_rank(results: Sequence[Any], gold_file: str) -> int | None:
    return next(
        (
            rank
            for rank, result in enumerate(results, start=1)
            if result.chunk.file_path == gold_file
        ),
        None,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="step-2-4-retrieval-offline")
    args = parser.parse_args()

    dev_ids = read_ids(TASKS_PATH)
    holdout_ids = read_ids(HOLDOUT_PATH)
    if set(dev_ids) & set(holdout_ids):
        raise RuntimeError("DEV and sealed HOLDOUT lists overlap")
    instances = load_dev_instances(dev_ids)

    rows: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []
    for instance_id in dev_ids:
        instance = instances[instance_id]
        gold_file = gold_file_from_patch(instance["patch"])
        spec = make_test_spec(instance)
        with DockerEnv(instance_id, spec.image, args.run_id) as env:
            index, stats = HybridCodeIndex.from_repository(env)
        index_rows.append({"instance_id": instance_id, **asdict(stats)})

        queries = {
            "full_issue": instance["problem_statement"],
            "short_query": short_query_proxy(instance["problem_statement"]),
        }
        for query_type, query in queries.items():
            result_count = max(1, len(index.chunks))
            bm25 = index.bm25.search(query, top_k=result_count)
            dense = index.dense.search(query, top_k=result_count)
            rrf = index.search(query, top_k=result_count)
            row = {
                "instance_id": instance_id,
                "gold_file": gold_file,
                "query_type": query_type,
                "bm25_rank": file_rank(bm25, gold_file),
                "dense_rank": file_rank(dense, gold_file),
                "rrf_rank": file_rank(rrf, gold_file),
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False))

    recalls: dict[str, dict[str, dict[str, float]]] = {}
    for query_type in ("full_issue", "short_query"):
        selected = [row for row in rows if row["query_type"] == query_type]
        recalls[query_type] = {}
        for method in ("bm25", "dense", "rrf"):
            recalls[query_type][method] = {
                f"recall_at_{cutoff}": sum(
                    row[f"{method}_rank"] is not None
                    and row[f"{method}_rank"] <= cutoff
                    for row in selected
                )
                / len(selected)
                for cutoff in (1, 3, 5)
            }

    report = {
        "dataset": DATASET,
        "split": SPLIT,
        "short_query_rule": (
            "first non-empty issue line, strip leading Markdown markers, "
            "truncate to 500 characters"
        ),
        "tasks": rows,
        "indexes": index_rows,
        "recalls": recalls,
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
