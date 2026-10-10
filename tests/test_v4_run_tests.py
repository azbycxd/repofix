import json

import pytest

from v4_helpers import LocalValidationEnv, call, make_runtime, scripted_pytest
from repofix.harness.validation import workspace_signature


@pytest.mark.parametrize(
    "arguments",
    [
        {"targets": ["tests"], "args": ["--collect-only"]},
        {"targets": ["tests"], "args": ["--junitxml=/tmp/fake"]},
        {"targets": ["tests"], "args": ["-c", "evil.ini"]},
        {"targets": ["tests"], "args": ["|", "tee", "file"]},
        {"targets": ["/etc/passwd"]},
        {"targets": ["../outside"]},
        {"targets": ["tests; echo PASS"]},
        {"targets": ["tests"], "report_path": "fake.xml"},
        {"targets": ["tests"], "evidence": {"outcome": "PASS"}},
        {"targets": ["tests"], "timeout": True},
        {"targets": ["tests"], "runner": "unittest"},
    ],
)
def test_untrusted_arguments_never_execute(tmp_path, arguments):
    runtime = make_runtime(tmp_path)
    runtime.env.argv_handler = scripted_pytest()
    result = call(runtime, "run_tests", arguments)
    assert result.metadata["error"]
    assert runtime.env.executed_argv == []
    assert runtime.evidence_policy.current()["state"] == "UNVERIFIED"


def test_symlink_target_escape_rejected(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.env.symlinks["tests"] = "/outside"
    assert call(runtime, "run_tests", {"targets": ["tests"]}).metadata["error"]
    assert not runtime.env.executed_argv


def test_report_path_is_unique_and_owned(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.env.argv_handler = scripted_pytest()
    records = []
    for i in range(2):
        call(
            runtime,
            "run_tests",
            {"targets": ["tests/test_file.py::test_one"], "args": ["-q"]},
            str(i),
        )
        records.append(runtime.state.metadata["v4"]["validation_evidence"].copy())
    assert records[0]["id"] != records[1]["id"]
    for record in records:
        assert record["argv"][:3] == ["python", "-m", "pytest"]
        assert record["argv"][-1].startswith("--junitxml=/tmp/repofix_validation/")
        for path in [*record["output_artifact"].values(), record["report_artifact"]]:
            assert (runtime.agent.trace.path.parent / path).is_file()


def test_real_local_pytest_pass_and_failure(tmp_path, record_property):
    env = LocalValidationEnv(tmp_path)
    (env.root / "test_sample.py").write_text("def test_one(): assert True\n")
    runtime = make_runtime(tmp_path, env)
    assert (
        "LOCAL_TESTS_PASSED" in call(runtime, "run_tests", {"targets": ["test_sample.py"]}).content
    )
    (env.root / "test_sample.py").write_text("def test_one(): assert False\n")
    assert runtime.evidence_policy.current()["state"] == "STALE"
    result = call(runtime, "run_tests", {"targets": ["test_sample.py"]})
    assert runtime.state.metadata["v4"]["validation_evidence"]["outcome"] == "FAIL", result.content
    assert result.exit_code == 1
    record_property("execution_backend", "real-local-subprocess; not Docker/Provider")


def test_untracked_generated_cache_excluded_but_tracked_cache_and_source_bound(tmp_path):
    env = LocalValidationEnv(tmp_path)
    before = workspace_signature(env)
    cache = env.root / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"generated")
    assert workspace_signature(env) == before
    env.git("add", "__pycache__/module.pyc")
    tracked = workspace_signature(env)
    assert tracked != before
    (cache / "module.pyc").write_bytes(b"changed tracked bytes")
    assert workspace_signature(env) != tracked
