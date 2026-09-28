"""Deterministically select five dev tasks and three sealed holdout tasks."""

import os
import random
from pathlib import Path

os.environ.setdefault("DATASETS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from datasets import load_dataset
from datasets.utils.logging import disable_progress_bar, set_verbosity_error
from huggingface_hub.utils.logging import set_verbosity_error as set_hub_verbosity_error
from unidiff import PatchSet


DATASET = "SWE-bench/SWE-bench_Verified"
SPLIT = "test"
RANDOM_SEED = 20260928
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def is_single_file_django_task(row: dict) -> bool:
    if row["repo"] != "django/django":
        return False
    try:
        return len(PatchSet(row["patch"])) == 1
    except Exception:
        return False


def main() -> None:
    set_verbosity_error()
    set_hub_verbosity_error()
    disable_progress_bar()
    rows = load_dataset(DATASET, split=SPLIT)
    candidate_ids = sorted(
        row["instance_id"] for row in rows if is_single_file_django_task(row)
    )
    chosen = random.Random(RANDOM_SEED).sample(candidate_ids, 8)

    (PROJECT_ROOT / "tasks.txt").write_text(
        "\n".join(chosen[:5]) + "\n",
        encoding="utf-8",
    )
    (PROJECT_ROOT / "holdout.txt").write_text(
        "\n".join(chosen[5:]) + "\n",
        encoding="utf-8",
    )

    identities = ("DEV",) * 5 + ("HOLDOUT",) * 3
    for identity, instance_id in zip(identities, chosen):
        print(f"{instance_id} {identity}")


if __name__ == "__main__":
    main()
