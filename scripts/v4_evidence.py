"""Append-only local evidence capture. No Provider is called by this module."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repofix.agent import RepoFixAgent  # noqa: E402
from repofix.core import TrajectoryWriter  # noqa: E402
from repofix.harness.config import HarnessConfig  # noqa: E402
from repofix.harness.fake import FakeEnv  # noqa: E402
from repofix.harness.model import FakeModelClient  # noqa: E402
from repofix.harness.runtime import Runtime  # noqa: E402
from repofix.harness.state import RunState  # noqa: E402

OUT = ROOT / "runs/v4-reliability-20261010"


def git(*args, cwd=ROOT):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def source_root():
    return Path(git("rev-parse", "--git-common-dir")).resolve().parent


def key():
    return os.environ.get("DEEPSEEK_API_KEY") or dotenv_values(source_root() / ".env").get(
        "DEEPSEEK_API_KEY", ""
    )


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def protected(root):
    names = ["FINAL_CONFIG.md", "tasks.txt", "holdout.txt"]
    names += git("ls-files", "dev_artifacts", cwd=root).splitlines()
    return {name: digest((root / name).read_bytes()) for name in names}


def record(label, command, *, extra_env=None, timeout=1800):
    folder = OUT / "commands" / label
    folder.mkdir(parents=True, exist_ok=False)
    redact = TrajectoryWriter(folder / "unused.jsonl", secrets=[key()])._redact_text
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUNBUFFERED="1")
    env.pop("DEEPSEEK_API_KEY", None)
    env.update(extra_env or {})
    started = time.monotonic()
    row = {
        "command": [str(a) for a in command],
        "started": stamp(),
        "git_sha": git("rev-parse", "HEAD"),
    }
    write_json(folder / "command.json", row)
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", None
        row["timed_out"] = True
    for name, value in (("stdout.log", stdout), ("stderr.log", stderr)):
        (folder / name).write_text(
            redact(value.decode("utf-8", errors="replace")), encoding="utf-8"
        )
    row.update(ended=stamp(), exit_code=code, wall_seconds=time.monotonic() - started)
    write_json(folder / "command.json", row)
    print(json.dumps({"label": label, **row}), flush=True)
    return row


def prepare():
    OUT.mkdir(parents=True, exist_ok=False)
    source = source_root()
    devs = (ROOT / "tasks.txt").read_text().split()
    holdout = set((ROOT / "holdout.txt").read_text().split())
    selected = ["django__django-16429", "django__django-15277", "django__django-13343"]
    assert set(selected) <= set(devs) and not holdout.intersection(selected)
    config = HarnessConfig.for_profile("v3")
    state = RunState()
    agent = RepoFixAgent(
        FakeEnv(), "contract", OUT / "unused.jsonl", "", "contract", config, FakeModelClient([])
    )
    schemas = Runtime(agent, state).registry.schemas
    old_report = (
        source
        / ".cache/interview-real-20261009/repo/docs/interview/20261009-real/evidence_manifest.json"
    )
    protection = dict(
        source_sha=git("rev-parse", "HEAD", cwd=source),
        source_status=git("status", "--porcelain", cwd=source),
        protected=protected(source),
        old_manifest_exists=old_report.exists(),
        old_manifest_sha=digest(old_report.read_bytes()) if old_report.exists() else None,
    )
    write_json(OUT / "source_protection.json", protection)
    plan = dict(
        created=stamp(),
        base_sha=git("rev-parse", "HEAD"),
        branch=git("branch", "--show-current"),
        selected_dev=selected,
        holdout_intersection=False,
        provider_key_present=bool(key()),
        python=sys.version,
        budget_usd=1.0,
        repeats=1,
        versions={
            name: importlib.metadata.version(name)
            for name in ("swebench", "datasets", "openai", "docker", "pytest")
        },
        old_config=asdict(config),
        old_state=asdict(RunState()),
        old_schemas_sha256=canonical_hash(schemas),
        protected_digest=canonical_hash(protection),
        no_push=True,
        no_merge=True,
    )
    write_json(OUT / "M0_baseline.json", plan)
    print(
        json.dumps({k: v for k, v in plan.items() if k not in ("old_config", "old_state")}),
        flush=True,
    )
    record("M0-before-offline", [sys.executable, "-m", "pytest", "-q"])
    record("M0-docker-version", ["docker", "version"], timeout=30)
    record("M0-docker-images", ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"])


def milestone_report(name):
    plan = json.loads((OUT / "M0_baseline.json").read_text())
    checks = []
    for path in sorted((OUT / "commands").glob(name + "-*/command.json")):
        row = json.loads(path.read_text())
        row["label"] = path.parent.name
        row["command"] = [
            arg.replace(str(ROOT), "$WORKTREE").replace(sys.executable, "$PYTHON")
            for arg in row["command"]
        ]
        output = (path.parent / "stdout.log").read_text()
        row["test_summary"] = next(
            (
                line
                for line in reversed(output.splitlines())
                if "passed" in line or "failed" in line
            ),
            None,
        )
        checks.append(row)
    protection = json.loads((OUT / "source_protection.json").read_text())
    unchanged = (
        protected(source_root()) == protection["protected"]
        and git("status", "--porcelain", cwd=source_root()) == protection["source_status"]
    )
    assert unchanged
    report = dict(
        milestone=name,
        created=stamp(),
        base_sha=plan["base_sha"],
        branch=git("branch", "--show-current"),
        selected_dev=plan["selected_dev"],
        versions=plan["versions"],
        python=plan["python"],
        provider_key_present=plan["provider_key_present"],
        provider_calls=0,
        source_and_protected_unchanged=unchanged,
        old_v3_schema_sha256=plan["old_schemas_sha256"],
        checks=checks,
        evidence_root=str(OUT.relative_to(ROOT)),
    )
    write_json(ROOT / "docs/v4" / f"{name}_baseline.json", report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--label")
    parser.add_argument("--milestone-report")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.prepare:
        prepare()
    elif args.milestone_report:
        milestone_report(args.milestone_report)
    else:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not args.label or not command:
            parser.error("--label and command required")
        row = record(args.label, command)
        raise SystemExit(0 if row["exit_code"] == 0 else 1)


if __name__ == "__main__":
    main()
