"""Read actual pytest JUnit/trace artifacts, never synthesize acceptance results."""

import csv
import hashlib
import json
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def property_value(text):
    if text in {"True", "true"}:
        return True
    if text in {"False", "false"}:
        return False
    if text in {"None", "null"}:
        return None
    return text


def junit_rows(path, group):
    rows = []
    for case in ET.parse(path).getroot().iter("testcase"):
        props = {
            p.get("name"): property_value(p.get("value", ""))
            for p in case.findall("./properties/property")
        }
        status = (
            "ERROR"
            if case.find("error") is not None
            else "FAIL"
            if case.find("failure") is not None
            else ("SKIPPED" if case.find("skipped") is not None else "PASS")
        )
        rows.append(
            {
                "producer": "pytest",
                "group": group,
                "case": case.get("classname", "") + "::" + case.get("name", ""),
                "status": status,
                "seconds": float(case.get("time", 0)),
                "properties": props,
                "junit_sha256": file_hash(path),
                "diagnostic": "\n".join(
                    (node.text or node.get("message", ""))
                    for node in case
                    if node.tag in {"error", "failure", "skipped"}
                )
                or None,
            }
        )
    return rows


def metrics_row(row):
    props = row["properties"]
    expected, observed = props.get("expected_validation"), props.get("observed_validation")
    measured = expected is not None and observed is not None and row["status"] != "SKIPPED"
    positive = {"PASS", "LOCAL_TESTS_PASSED"}
    expected_stuck, observed_stuck = props.get("stuck_expected"), props.get("stuck_observed")
    return {
        "group": row["group"],
        "case": row["case"],
        "status": row["status"],
        "backend": props.get("execution_backend", "offline-protocol"),
        "seconds": row["seconds"],
        "expected_validation": expected,
        "observed_validation": observed,
        "false_local_pass": int(observed in positive)
        if measured and expected not in positive
        else None,
        "false_rejection": int(observed not in positive)
        if measured and expected in positive
        else None,
        "recovery_correct": props.get("recovery_correct"),
        "stuck_expected": expected_stuck,
        "stuck_observed": observed_stuck,
        "false_stuck": int(observed_stuck is True)
        if expected_stuck is False and observed_stuck is not None
        else None,
        "cleanup_correct": props.get("cleanup_correct"),
        "progress_seconds": props.get("progress_seconds"),
        "progress_feedback_bytes": props.get("progress_feedback_bytes"),
    }


def fraction(rows, key):
    values = [r[key] for r in rows if r[key] is not None]
    return {"count": sum(int(v) for v in values) if values else None, "measured_cases": len(values)}


def summarize_faults(rows):
    metrics = [metrics_row(row) for row in rows]
    return {
        "cases": {
            name: sum(r["status"] == name for r in rows)
            for name in ("PASS", "FAIL", "ERROR", "SKIPPED")
        },
        "false_local_pass": fraction(metrics, "false_local_pass"),
        "false_rejection": fraction(metrics, "false_rejection"),
        "recovery_correct": fraction(metrics, "recovery_correct"),
        "false_stuck": fraction(metrics, "false_stuck"),
        "cleanup_correct": fraction(metrics, "cleanup_correct"),
        "unmeasured_failed_cases": [
            r["case"] for r in rows if r["status"] in {"FAIL", "ERROR"} and not r["properties"]
        ],
        "not_a_benchmark": "Predeclared small fixtures; each metric uses its own measured denominator.",
    }


def trace_overheads(root):
    measurements = []
    errors = []
    for path in sorted(Path(root).rglob("*.jsonl")):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            try:
                row = json.loads(line)
            except ValueError as exc:
                errors.append(
                    {"path": str(path.relative_to(root)), "line": number, "error": str(exc)}
                )
                continue
            if row.get("type") not in {"checkpoint", "progress", "tool_execution"}:
                continue
            measurements.append(
                {
                    "path": str(path.relative_to(root)),
                    "type": row["type"],
                    "backend": row.get("execution_backend", "NOT_MEASURED"),
                    "duration": row.get("checkpoint_duration")
                    if row["type"] == "checkpoint"
                    else row.get("duration_seconds"),
                    "snapshot_bytes": row.get("snapshot_bytes"),
                    "feedback_bytes": row.get("feedback_bytes"),
                }
            )
    groups = {}
    for row in measurements:
        key = row["backend"] + "/" + row["type"]
        groups.setdefault(key, []).append(row)
    aggregates = {}
    for key, samples in groups.items():
        durations = [s["duration"] for s in samples if s["duration"] is not None]
        sizes = [s["snapshot_bytes"] for s in samples if s["snapshot_bytes"] is not None]
        aggregates[key] = {
            "samples": len(samples),
            "median_seconds": statistics.median(durations) if durations else None,
            "max_snapshot_bytes": max(sizes) if sizes else None,
        }
    return {"measurements": measurements, "groups": aggregates, "parse_errors": errors}


def write_reports(root, rows, metadata):
    root = Path(root)
    metrics = [metrics_row(row) for row in rows]
    (root / "fault_results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    with (root / "metrics.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(
                metrics_row({"properties": {}, "group": "", "case": "", "status": "", "seconds": 0})
            ),
        )
        writer.writeheader()
        writer.writerows(
            {k: "NOT_MEASURED" if v is None else v for k, v in row.items()} for row in metrics
        )
    summary = {
        **metadata,
        "fault_metrics": summarize_faults(rows),
        "overheads": trace_overheads(root / "traces"),
    }
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    manifest = {"schema_version": 1, "run_id": root.name, **metadata, "artifacts": []}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "evidence_manifest.json":
            manifest["artifacts"].append(
                {
                    "path": str(path.relative_to(root)),
                    "bytes": path.stat().st_size,
                    "sha256": file_hash(path),
                }
            )
    (root / "evidence_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    return summary
