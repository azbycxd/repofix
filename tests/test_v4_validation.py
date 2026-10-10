import json
from unittest.mock import patch

import pytest

from repofix.env import ArgvExecutionResult
from repofix.harness.validation import EvidencePolicy, classify, parse_junit
from v4_helpers import call, junit, make_runtime, scripted_pytest


@pytest.mark.parametrize(
    "command", ["echo 'pytest passed'", "pytest tests | tee log", "pytest || true"]
)
def test_bash_text_cannot_create_evidence(tmp_path, command):
    runtime = make_runtime(tmp_path, background_shell=False)
    runtime.env.commands[command] = ("1 passed", 0, False, 0)
    call(runtime, "bash", {"command": command})
    assert runtime.state.metadata["v4"]["local_validation"]["state"] == "UNVERIFIED"
    assert "validation_evidence" not in runtime.state.metadata["v4"]
    assert call(runtime, "submit").metadata["hook_blocked"]


@pytest.mark.parametrize(
    "kind,code,timed_out,expected",
    [
        ("pass", 0, False, "PASS"),
        ("fail", 1, False, "FAIL"),
        ("fail", 0, False, "FAIL"),
        ("skip", 0, False, "INCONCLUSIVE"),
        ("empty", 5, False, "INCONCLUSIVE"),
        ("pass", None, False, "INCONCLUSIVE"),
        ("pass", 124, True, "INCONCLUSIVE"),
        ("fail", 2, False, "INCONCLUSIVE"),
    ],
)
def test_outcome_contract(tmp_path, kind, code, timed_out, expected, record_property):
    runtime = make_runtime(tmp_path)
    runtime.env.argv_handler = scripted_pytest(kind, code, timed_out)
    result = call(runtime, "run_tests", {"targets": ["tests"]})
    evidence = runtime.state.metadata["v4"]["validation_evidence"]
    assert evidence["outcome"] == expected, result.content
    assert evidence["tool_call_id"] == "call-1" and evidence["source"] == "harness_run_tests"
    record_property("expected_validation", expected)
    record_property("observed_validation", evidence["outcome"])
    record_property("failure_injection", expected != "PASS")


@pytest.mark.parametrize("method", ["bash", "apply_patch"])
def test_pass_then_edit_is_stale(tmp_path, method):
    runtime = make_runtime(tmp_path, background_shell=False)
    runtime.env.argv_handler = scripted_pytest()
    call(runtime, "run_tests", {"targets": ["tests"]})
    assert runtime.evidence_policy.current()["state"] == "LOCAL_TESTS_PASSED"
    if method == "bash":

        def edit(env):
            env.files["example.py"] = "VALUE = 2\n"
            return ("", 0, False, 0)

        runtime.env.commands["edit"] = edit
        call(runtime, "bash", {"command": "edit"})
    else:
        call(runtime, "view", {"path": "example.py", "start_line": 1, "end_line": 1})
        call(
            runtime,
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** Update File: example.py\n@@\n-VALUE = 1\n+VALUE = 2\n*** End Patch"
            },
        )
    assert runtime.evidence_policy.current()["state"] == "STALE"
    assert call(runtime, "submit").metadata["hook_blocked"]


def test_test_mutation_during_validation_is_not_pass(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.env.argv_handler = scripted_pytest(mutate=True)
    call(runtime, "run_tests", {"targets": ["tests"]})
    assert runtime.state.metadata["v4"]["validation_evidence"]["outcome"] == "INCONCLUSIVE"


def test_child_pass_is_advice_not_evidence(tmp_path):
    runtime = make_runtime(tmp_path, verify_on_submit=True, subagents="verify")
    with patch(
        "repofix.harness.tools.submit.run_subagent",
        return_value={"verdict": "PASS", "evidence": "trust me"},
    ):
        result = call(runtime, "submit")
    assert result.metadata["hook_blocked"]
    assert runtime.evidence_policy.current()["state"] == "UNVERIFIED"
    assert runtime.state.last_validation is None


def test_forced_submit_does_not_mean_verified_or_resolved(tmp_path):
    runtime = make_runtime(tmp_path)
    for i in range(3):
        assert call(runtime, "submit", identifier=str(i)).metadata["hook_blocked"]
    assert not runtime.state.submitted
    result = call(runtime, "submit", identifier="last")
    assert runtime.state.submitted and runtime.state.termination == "submit_forced"
    assert runtime.state.metadata["v4"]["local_validation"]["state"] == "UNVERIFIED"
    assert "independent Judge" in result.content


def test_local_pass_is_not_external_acceptance(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.env.argv_handler = scripted_pytest()
    call(runtime, "run_tests", {"targets": ["unrelated_tests"]})
    assert not call(runtime, "submit").metadata.get("hook_blocked")
    assert runtime.evidence_policy.current()["state"] == "LOCAL_TESTS_PASSED"
    # Deterministic independent hidden criterion intentionally differs from local scope.
    hidden_judge_resolved = "VALUE = 2" in runtime.env.files["example.py"]
    assert hidden_judge_resolved is False


def test_report_integrity_and_missing_report(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.env.argv_handler = scripted_pytest(missing=True)
    call(runtime, "run_tests", {"targets": ["tests"]})
    evidence = runtime.state.metadata["v4"]["validation_evidence"]
    assert evidence["outcome"] == "INCONCLUSIVE" and evidence["parse_error"]
    evidence["outcome"] = "PASS"
    assert runtime.evidence_policy.current()["state"] == "UNVERIFIED"
    with pytest.raises(ValueError):
        parse_junit(b'<testsuite tests="2"><testcase/></testsuite>')
    with pytest.raises(ValueError):
        parse_junit(b'<!DOCTYPE boom><testsuite tests="0"/>')


def test_no_module_pytest_is_environment_uncertainty():
    result = ArgvExecutionResult("", "No module named pytest", 1, False, 0)
    assert classify(result, None, True, "ENV_ERROR: pytest unavailable") == "INCONCLUSIVE"
