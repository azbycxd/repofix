from pathlib import Path

import pytest

from repofix.harness.experiment import VARIANTS, run_experiment
from repofix.harness.report import render_report


def test_fake_experiment_entire_pipeline(tmp_path):
    root = Path(__file__).resolve().parents[1]
    rows = run_experiment(
        root / "tasks/fake_tasks.json", list(VARIANTS), 1, tmp_path / "experiment", fake=True
    )
    assert len(rows) == 4 and all(r["resolved"] and r["submitted"] for r in rows), rows
    assert rows[2]["subagent_calls"] == 2
    assert rows[0]["subagent_calls"] == rows[1]["subagent_calls"] == 0
    report = render_report(rows)
    assert (
        "FAKE OFFLINE FIXTURE" in report and "P50/P95" in report and "Task result matrix" in report
    )
    assert len((tmp_path / "experiment/results.jsonl").read_text().splitlines()) == 4


def test_holdout_refused_before_any_run(tmp_path):
    root = Path(__file__).resolve().parents[1]
    holdout = tmp_path / "holdout.txt"
    holdout.write_text("offline-example\n")
    with pytest.raises(ValueError, match="HOLDOUT"):
        run_experiment(
            root / "tasks/fake_tasks.json",
            ["v1"],
            1,
            tmp_path / "output",
            True,
            holdout_path=holdout,
        )
    assert not (tmp_path / "output").exists()


def test_experiment_setup_error_is_not_interruption(tmp_path, monkeypatch):
    def broken_environment():
        raise RuntimeError("fixture setup failed")

    monkeypatch.setattr("repofix.harness.experiment.fake_env", broken_environment)
    root = Path(__file__).resolve().parents[1]
    rows = run_experiment(
        root / "tasks/fake_tasks.json", ["v3-single"], 1, tmp_path / "experiment", fake=True
    )
    assert rows[0]["termination_reason"] == "runtime_error"
    assert "fixture setup failed" in rows[0]["error"]
    assert "runtime_error | 1" in render_report(rows)
