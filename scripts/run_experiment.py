import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repofix.harness.experiment import run_experiment
from repofix.harness.report import render_report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--variants", default="v1,v3-single,v3-multi,v3-nocompact")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fake", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.fake:
            load_dotenv()
        rows = run_experiment(
            args.tasks,
            args.variants.split(","),
            args.repeats,
            args.out,
            args.fake,
            "" if args.fake else os.environ.get("DEEPSEEK_API_KEY", ""),
        )
        (args.out / "report.md").write_text(render_report(rows), encoding="utf-8")
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return 1 if any(row.get("error") for row in rows) else 0
    except Exception as exc:
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        print(
            "Experiment error: " + (str(exc).replace(key, "[REDACTED]") if key else str(exc)),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
