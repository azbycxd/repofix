"""Explicit task-generation utility; not invoked by ordinary Agent runs."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repofix.tasks.build import build_task
from repofix.tasks.docker import docker_evaluator
from repofix.tasks.spec import load_document


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--execute", action="store_true", help="Allow cloning/building trusted seed repositories"
    )
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("task generation requires --execute (network + trusted Docker builds)")
    entries = load_document(args.input)
    if not isinstance(entries, list):
        parser.error("seed must be a list")
    specs = [asdict(build_task(entry, docker_evaluator)) for entry in entries]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(specs, ensure_ascii=False, indent=2) + "\n")
    print(
        "Generated tasks. Human review required: confirm descriptions do not reveal implementation before experiments."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
