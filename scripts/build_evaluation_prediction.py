"""Build one SWE-bench prediction from a saved full Agent patch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from repofix.evaluation import build_evaluation_patch  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--full-patch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="deepseek-flash")
    args = parser.parse_args()

    result = build_evaluation_patch(args.full_patch.read_text(encoding="utf-8"))
    prediction = [
        {
            "instance_id": args.instance_id,
            "model_patch": result.patch,
            "model_name_or_path": args.model,
        }
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(prediction, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"EVALUATION_PATCH_CHARS={len(result.patch)}")
    print("FILTERED_TEST_PATHS=" + ",".join(result.filtered_test_paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
