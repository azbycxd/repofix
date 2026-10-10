"""One command runs actual pytest suites and derives the reliability evidence matrix."""

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repofix.core import TrajectoryWriter  # noqa: E402
from repofix.harness.config import HarnessConfig, serialize_config  # noqa: E402
from repofix.harness.evidence import junit_rows, write_reports  # noqa: E402


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def source_tree_hash():
    digest = hashlib.sha256()
    for folder in ("src", "tests", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def run_command(folder, name, argv, timeout=1800):
    output = folder / "commands" / name
    output.mkdir(parents=True, exist_ok=False)
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        PYTHONUNBUFFERED="1",
        V4_EVIDENCE_DIR=str(folder / "traces" / name),
    )
    key = env.pop("DEEPSEEK_API_KEY", "")
    redact = TrajectoryWriter(output / "unused", secrets=[key])._redact_text
    started = time.monotonic()
    command = {
        "argv": argv,
        "started": datetime.now(timezone.utc).isoformat(),
        "code_sha": git("rev-parse", "HEAD"),
        "source_tree_sha256": source_tree_hash(),
    }
    (output / "command.json").write_text(json.dumps(command, indent=2))
    process = subprocess.Popen(
        argv,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGINT)
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        command["timeout"] = True
    for suffix, value in (("stdout.log", stdout), ("stderr.log", stderr)):
        (output / suffix).write_text(redact(value.decode(errors="replace")))
    command.update(
        exit_code=process.returncode,
        wall_seconds=time.monotonic() - started,
        ended=datetime.now(timezone.utc).isoformat(),
    )
    (output / "command.json").write_text(json.dumps(command, indent=2))
    print(
        json.dumps(
            {
                "command": name,
                "exit_code": process.returncode,
                "wall_seconds": command["wall_seconds"],
            }
        ),
        flush=True,
    )
    return command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents:
        parser.error("evidence output must be a new directory under this worktree")
    output.mkdir(parents=True, exist_ok=False)
    (output / "junit").mkdir()
    (output / "traces").mkdir()
    blockers, commands, rows = [], [], []
    config = serialize_config(HarnessConfig.for_profile("v4"))
    manifest = {
        "created": datetime.now(timezone.utc).isoformat(),
        "code_sha": git("rev-parse", "HEAD"),
        "source_tree_sha256": source_tree_hash(),
        "git_status": git("status", "--porcelain"),
        "python": sys.version,
        "platform": platform.platform(),
        "config": config,
        "config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "provider_calls": 0,
        "holdout_agent_runs": 0,
        "commands": commands,
    }
    for name, argv in [
        ("offline", [sys.executable, "-m", "pytest", "-q"]),
        (
            "docker",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--docker",
                "-m",
                "docker",
                *[
                    str(p.relative_to(ROOT))
                    for p in sorted((ROOT / "tests").glob("test_v4_*docker.py"))
                ],
            ],
        ),
    ]:
        if name == "docker":
            if not args.docker:
                blockers.append("Docker suite NOT_REQUESTED; skipped tests are not PASS.")
                continue
            probe = run_command(output, "docker-version", ["docker", "version"], timeout=30)
            commands.append(probe)
            if probe["exit_code"]:
                blockers.append(
                    "BLOCKED_DOCKER: docker version failed; see commands/docker-version."
                )
                continue
        report = output / "junit" / (name + ".xml")
        argv += ["--junitxml=" + str(report), "-o", "junit_family=xunit1"]
        commands.append(run_command(output, name, argv))
        if report.exists():
            rows.extend(junit_rows(report, name))
        else:
            blockers.append(name + " did not generate JUnit; NOT VERIFIED.")
    (output / "blockers.md").write_text("\n".join(blockers) + "\n" if blockers else "none\n")
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    summary = write_reports(output, rows, manifest)
    print(json.dumps(summary["fault_metrics"], ensure_ascii=False), flush=True)
    raise SystemExit(1 if blockers or any(c["exit_code"] != 0 for c in commands) else 0)


if __name__ == "__main__":
    main()
