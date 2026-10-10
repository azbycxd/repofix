import json
import importlib.util
from pathlib import Path

from repofix.agent import RepoFixAgent
from repofix.harness.config import HarnessConfig
from repofix.harness.evidence import (
    file_hash,
    junit_rows,
    metrics_row,
    summarize_faults,
    write_reports,
)
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from test_v4_progress import run_script
from v4_helpers import artifact_dir, call, make_runtime, scripted_pytest


def test_trace_producers_and_three_verdict_boundaries(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.env.argv_handler = scripted_pytest()
    call(runtime, "run_tests", {"targets": ["tests"]})
    call(runtime, "submit")
    records = [json.loads(line) for line in runtime.agent.trace.path.read_text().splitlines()]
    assert all(r["producer"] == "harness" and r["schema_version"] == 4 for r in records)
    assert all(r["judge_verdict"] == "NOT_JUDGED" for r in records)
    tool = records[-1]
    assert (
        tool["validation_state"] == "LOCAL_TESTS_PASSED" and tool["submission_state"] == "submitted"
    )
    assert tool["tool_call_id"] == "call-1" and tool["validation_evidence_id"]
    assert tool["model_backend"] == "fake" and tool["execution_backend"] == "fake"


def test_model_proposals_are_not_harness_execution_facts(tmp_path):
    agent, result, _ = run_script(
        tmp_path, [{"calls": [("grep", {"pattern": "VALUE"})]}], max_steps=1
    )
    records = [json.loads(line) for line in agent.trace.path.read_text().splitlines()]
    step = next(r for r in records if r["type"] == "step")
    assert step["tool_calls"][0]["producer"] == "assistant"
    assert step["observation"][0]["producer"] == "harness"
    assert (
        result.reliability["candidate_patch_sha256"]
        and result.reliability["judge_verdict"] == "NOT_JUDGED"
    )


def test_provider_error_cost_is_unknown_not_zero(tmp_path):
    _, result, _ = run_script(tmp_path, [RuntimeError("connection lost")])
    assert result.reliability["estimated_total_usd"] is None
    assert result.reliability["model_requests"] == 1
    assert result.reliability["completed_provider_responses"] == 0


def test_trace_redacts_keys_jwt_authorization_and_host_paths(tmp_path):
    key = "synthetic-private-value-for-test"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.synthetic_signature"
    agent = RepoFixAgent(
        FakeEnv(),
        key + " " + jwt + " Authorization: Bearer fake-token " + str(Path.home()),
        artifact_dir(tmp_path) / "trace.jsonl",
        key,
        "fixture",
        HarnessConfig.for_profile("v4"),
        FakeModelClient([{}, {}]),
    )
    agent.run()
    text = agent.trace.path.read_text()
    for forbidden in [key, jwt, "fake-token", str(Path.home())]:
        assert forbidden not in text
    assert "[REDACTED" in text


def test_report_parser_preserves_failure_skip_missing_and_measured_zero(tmp_path):
    report = tmp_path / "tests.xml"
    report.write_text("""<testsuites><testsuite>
      <testcase classname="matrix" name="negative" time="0.1"><properties>
        <property name="expected_validation" value="FAIL"/><property name="observed_validation" value="FAIL"/>
      </properties></testcase>
      <testcase classname="matrix" name="positive" time="0.2"><properties>
        <property name="expected_validation" value="PASS"/><property name="observed_validation" value="PASS"/>
      </properties></testcase>
      <testcase classname="matrix" name="unknown" time="0"><failure>injected real failure</failure></testcase>
      <testcase classname="matrix" name="not_run" time="0"><skipped>Docker absent</skipped></testcase>
    </testsuite></testsuites>""")
    rows = junit_rows(report, "fixture-parser-not-acceptance")
    summary = summarize_faults(rows)
    assert summary["false_local_pass"] == {"count": 0, "measured_cases": 1}
    assert summary["false_rejection"] == {"count": 0, "measured_cases": 1}
    assert summary["recovery_correct"] == {"count": None, "measured_cases": 0}
    assert summary["cases"] == {"PASS": 2, "FAIL": 1, "SKIPPED": 1, "ERROR": 0}
    assert metrics_row(rows[2])["false_local_pass"] is None
    (tmp_path / "traces").mkdir()
    write_reports(tmp_path, rows, {"code_sha": "fixture-not-production"})
    manifest = json.loads((tmp_path / "evidence_manifest.json").read_text())
    for item in manifest["artifacts"]:
        assert file_hash(tmp_path / item["path"]) == item["sha256"]
    assert "NOT_MEASURED" in (tmp_path / "metrics.csv").read_text()


def test_metric_parser_exposes_false_pass_and_false_rejection():
    row = {
        "group": "fixture",
        "case": "false-pass",
        "status": "FAIL",
        "seconds": 0,
        "properties": {"expected_validation": "FAIL", "observed_validation": "PASS"},
    }
    assert metrics_row(row)["false_local_pass"] == 1
    row["properties"] = {"expected_validation": "PASS", "observed_validation": "INCONCLUSIVE"}
    assert metrics_row(row)["false_rejection"] == 1


def test_v4_runner_loads_only_public_selected_fields(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/run_agent.py"
    spec = importlib.util.spec_from_file_location("v4_runner_test", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    reads = []

    class Dataset:
        column_names = [
            "instance_id",
            "problem_statement",
            "image",
            "patch",
            "test_patch",
            "FAIL_TO_PASS",
        ]

        def __getitem__(self, name):
            assert name == "instance_id"
            return ["selected-dev", "nonselected-id"]

        def select(self, indices):
            assert indices == [0]
            return self

        def select_columns(self, columns):
            reads.extend(columns)
            return [
                {
                    "instance_id": "selected-dev",
                    "problem_statement": "public only",
                    "image": "fixture",
                }
            ]

    monkeypatch.setattr(runner, "load_dataset", lambda *a, **k: Dataset())
    row = runner.load_instances(["selected-dev"], public_only=True)["selected-dev"]
    assert not set(reads).intersection({"patch", "test_patch", "FAIL_TO_PASS"})
    assert row["FAIL_TO_PASS"] == [] and row["eval_script"] == "true"
