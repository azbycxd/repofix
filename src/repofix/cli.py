"""Command-line entry point for local Python repository repair."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .harness.config import HarnessConfig
from .harness.resume import resume_run
from .local_runner import run_local_repository


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("local-%Y%m%dT%H%M%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repofix")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--issue", "--task", dest="issue", required=True)
    parser.add_argument("--kind", choices=("bugfix", "feature"), default="bugfix")
    parser.add_argument("--profile", choices=("v1", "v3", "v4"), default="v1")
    parser.add_argument("--permissions-file")
    parser.add_argument("--hooks-file")
    parser.add_argument("--config", type=Path, help="JSON HarnessConfig overrides (no credentials)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "resume":
        resume_parser = argparse.ArgumentParser(prog="repofix resume")
        resume_parser.add_argument("run_dir", type=Path)
        resume_args = resume_parser.parse_args(argv[1:])
        load_dotenv(Path.cwd() / ".env", override=False)
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            print("RepoFix error: DEEPSEEK_API_KEY is not set", file=sys.stderr)
            return 2
        try:
            result = resume_run(resume_args.run_dir, key)
            print(f"{result.status}: {result.trajectory_path}")
            return 0 if result.submitted else 1
        except Exception as exc:
            print("RepoFix resume error: " + str(exc).replace(key, "[REDACTED]"), file=sys.stderr)
            return 1
    args = build_parser().parse_args(argv)
    load_dotenv(Path.cwd() / ".env", override=False)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("RepoFix error: DEEPSEEK_API_KEY is not set", file=sys.stderr)
        return 2
    run_id = args.run_id or _default_run_id()
    output_dir = args.output_dir or Path.home() / ".repofix" / "runs" / run_id
    try:
        overrides = json.loads(args.config.read_text()) if args.config else {}
        if not isinstance(overrides, dict) or "profile" in overrides:
            raise ValueError("config must be a JSON object without profile; use --profile")
        overrides.update(evaluation_mode=False, task_kind=args.kind)
        if args.permissions_file:
            overrides["permissions_file"] = args.permissions_file
        if args.hooks_file:
            overrides["hooks_file"] = args.hooks_file
        outcome = run_local_repository(
            repository=args.repo,
            issue=args.issue,
            output_dir=output_dir,
            api_key=api_key,
            run_id=run_id,
            config=HarnessConfig.for_profile(args.profile, **overrides),
        )
    except Exception as exc:
        safe_error = str(exc).replace(api_key, "[REDACTED]")
        print(f"RepoFix error: {safe_error}", file=sys.stderr)
        return 1

    print("=== FULL DIFF ===")
    print(outcome.full_diff or "(empty)")
    print("=== PRODUCTION-ONLY DIFF ===")
    print(outcome.production_diff or "(empty)")
    print("=== SUMMARY ===")
    print(json.dumps(outcome.summary, ensure_ascii=False, indent=2))
    return 0 if outcome.summary["submitted"] and not outcome.summary["error"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
