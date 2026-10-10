"""Publish a bounded, redacted evidence copy; never rewrite original run artifacts."""

import argparse
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from v4_evidence import OUT, ROOT, key, protected, source_root

from repofix.core import TrajectoryWriter
from repofix.harness.evidence import file_hash, junit_rows, trace_overheads, write_reports


def git(*args, cwd=ROOT):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def redactor():
    writer = TrajectoryWriter(OUT / "unused-audit.jsonl", secrets=[key()])
    replacements = [
        (str(ROOT), "$WORKTREE"),
        (str(source_root()), "$SOURCE"),
        (str(Path.home()), "$HOME"),
        ("/tmp/pytest-of-" + Path.home().name, "$PYTEST_TMP"),
    ]

    def clean(text):
        text = writer._redact_text(text)
        text = re.sub(
            r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED_JWT]", text
        )
        for original, replacement in replacements:
            text = text.replace(original, replacement)
        return text

    return clean


def publishable(path, relative):
    # Recovery blobs stay byte-exact locally. Redacting state would invalidate
    # their hashes, so publish only their original hash and fault receipts.
    if "checkpoints" in relative.parts or "workspaces" in relative.parts:
        return False
    if path.name in {"source_protection.json", "manifest.json"} and len(relative.parts) == 1:
        return False
    return path.suffix in {".json", ".jsonl", ".xml", ".log", ".patch", ".csv", ".md", ".txt"}


def clean_document(path, text, clean):
    def value(item):
        if isinstance(item, str):
            return clean(item)
        if isinstance(item, list):
            return [value(part) for part in item]
        if isinstance(item, dict):
            return {name: value(part) for name, part in item.items()}
        return item

    if path.suffix == ".json":
        try:
            return json.dumps(value(json.loads(text)), ensure_ascii=False, indent=2) + "\n"
        except ValueError:
            # Intentionally damaged checkpoint fixtures remain damaged evidence.
            return clean(text)
    if path.suffix == ".jsonl":
        return "".join(
            json.dumps(value(json.loads(line)), ensure_ascii=False) + "\n"
            for line in text.splitlines()
            if line
        )
    if path.suffix == ".xml":
        try:
            tree = ET.fromstring(text)
        except ET.ParseError:
            # Malformed runner reports are intentionally retained negative evidence.
            return clean(text)
        for node in tree.iter():
            node.text = clean(node.text) if node.text else node.text
            node.tail = clean(node.tail) if node.tail else node.tail
            node.attrib.update({name: clean(part) for name, part in node.attrib.items()})
        return ET.tostring(tree, encoding="unicode")
    return clean(text)


def protection_audit():
    previous = json.loads((OUT / "source_protection.json").read_text())
    original = source_root()
    old = (
        original
        / ".cache/interview-real-20261009/repo/docs/interview/20261009-real/evidence_manifest.json"
    )
    checks = {
        "source_head_unchanged": git("rev-parse", "HEAD", cwd=original) == previous["source_sha"],
        "source_status_unchanged": git("status", "--porcelain", cwd=original)
        == previous["source_status"],
        "protected_files_unchanged": protected(original) == previous["protected"],
        "v4_protected_files_unchanged": protected(ROOT) == previous["protected"],
        "prior_experiment_manifest_unchanged": (
            file_hash(old) == previous["old_manifest_sha"]
            if old.exists()
            else not previous["old_manifest_exists"]
        ),
    }
    if not all(checks.values()):
        raise RuntimeError("Original data protection audit failed: " + str(checks))
    return checks


def copy_evidence(destination, clean):
    originals, published, secret_hits = [], [], []
    secret = key().encode()
    for path in sorted(OUT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(OUT)
        item = {"path": str(relative), "bytes": path.stat().st_size, "sha256": file_hash(path)}
        originals.append(item)
        data = path.read_bytes()
        if secret and secret in data:
            secret_hits.append(str(relative))
        if not publishable(path, relative):
            continue
        text = data.decode("utf-8", errors="strict")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        safe = clean_document(path, text, clean)
        target.write_text(safe)
        published.append(
            {
                "path": str(target.relative_to(destination.parent)),
                "original_path": str(relative),
                "original_sha256": item["sha256"],
                "sha256": file_hash(target),
                "redacted_or_reformatted": safe != text,
            }
        )
    if secret_hits:
        raise RuntimeError(
            "Actual credential found in original artifacts; paths: " + str(secret_hits)
        )
    return originals, published


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=ROOT / "docs/v4")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--refresh-reports", action="store_true")
    args = parser.parse_args()
    destination = args.destination.resolve()
    if ROOT not in destination.parents:
        parser.error("destination must be inside the isolated worktree")
    clean = redactor()
    checks = protection_audit()
    if args.check_only:
        secret = key().encode()
        count = 0
        for path in OUT.rglob("*"):
            if not path.is_file():
                continue
            data = path.read_bytes()
            if secret and secret in data:
                raise RuntimeError("Actual key found in " + str(path.relative_to(OUT)))
            if publishable(path, path.relative_to(OUT)):
                clean_document(path, data.decode("utf-8"), clean)
                count += 1
        print(
            json.dumps(
                {"protection": checks, "publishable_checked": count, "actual_key_absent": True}
            )
        )
        return
    evidence = destination / "evidence"
    if args.refresh_reports:
        previous = json.loads((destination / "summary.json").read_text())
        originals, published = previous["original_artifacts"], previous["published_artifacts"]
        for item in originals:
            if file_hash(OUT / item["path"]) != item["sha256"]:
                raise RuntimeError("Original evidence changed: " + item["path"])
        for item in published:
            if file_hash(destination / item["path"]) != item["sha256"]:
                raise RuntimeError("Published evidence changed: " + item["path"])
    else:
        evidence.mkdir(parents=True, exist_ok=False)
        originals, published = copy_evidence(evidence, clean)
    holdout = (ROOT / "holdout.txt").read_text().split()
    traces = sorted((OUT / "dev-final/traces").glob("*/trajectory.jsonl"))
    for path in traces:
        text = path.read_text()
        if any(instance in text for instance in holdout):
            raise RuntimeError("HOLDOUT ID found in live Agent trace")
    rows = []
    for name in ("M6-offline", "M6-all-docker", "M6-local-sandbox"):
        path = evidence / "junit" / (name + ".xml")
        if path.exists():
            rows.extend(junit_rows(path, name))
    metadata = {
        "run_id": OUT.name,
        "code_sha": git("rev-parse", "HEAD"),
        "created": datetime.now(UTC).isoformat(),
        "raw_root": str(OUT.relative_to(ROOT)),
        "original_artifacts": originals,
        "published_artifacts": published,
        "commands": [
            {
                "path": str(path.relative_to(OUT)),
                **json.loads(clean_document(path, path.read_text(), clean)),
            }
            for path in sorted(OUT.rglob("command.json"))
        ],
        "audit": {**checks, "actual_key_absent": True, "live_holdout_ids_absent": True},
        "live_trajectory_count": len(traces),
        "measured_overheads": trace_overheads(OUT / "traces"),
        "dev_overheads": trace_overheads(OUT / "dev-final/traces"),
        "overhead_note": "Use measured_overheads and dev_overheads; traces are not duplicated at docs root.",
        "holdout_agent_runs": 0,
        "no_push": True,
        "no_merge": True,
    }
    # write_reports scans traces/ for overheads. Published raw traces retain their
    # original hierarchy; do not duplicate or relocate them just for aggregation.
    (destination / "audit.json").write_text(json.dumps(metadata["audit"], indent=2) + "\n")
    summary = write_reports(destination, rows, metadata)
    print(
        json.dumps(
            {
                "audit": metadata["audit"],
                "fault_metrics": summary["fault_metrics"],
                "raw_artifacts": len(originals),
                "published_artifacts": len(published),
            }
        )
    )


if __name__ == "__main__":
    main()
