import base64
import io
import json
import tarfile
from unittest.mock import patch

import pytest

from repofix.agent import RepoFixAgent
from repofix.harness.checkpoint_v4 import V4CheckpointStore, validate_snapshot
from repofix.harness.config import HarnessConfig
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.state import RunState
from repofix.harness.validation import workspace_signature
from v4_helpers import artifact_dir, call, make_runtime, scripted_pytest


def pending_state():
    state = RunState(step=2)
    state.budget.estimated_cost = 0.03
    state.budget.provider_calls = 2
    state.budget.prompt_tokens = 123
    state.pending_calls = [
        {"id": "stable-id", "name": "bash", "arguments": '{"command":"side-effect"}'}
    ]
    state.messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "stable-id",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        }
    ]
    return state


def test_generations_match_workspace_budget_and_pending_no_replay(tmp_path, record_property):
    env, state = FakeEnv(), pending_state()
    store = V4CheckpointStore(artifact_dir(tmp_path))
    store.save(state, env, "pre_dispatch")
    env.files["example.py"] = "VALUE = 2\n"
    state.step = 3
    store.save(state, env, "post_mutation")
    target = FakeEnv()
    restored = store.resume(target)
    json.dumps(store.loaded_manifest)  # loaded audit manifest remains plain immutable JSON data
    assert target.files == env.files and not target.executed
    assert restored.step == 3 and restored.budget == state.budget
    assert len([m for m in restored.messages if m.get("tool_call_id") == "stable-id"]) == 1
    assert "INTERRUPTED_UNKNOWN" in restored.messages[-1]["content"]
    assert not restored.pending_calls
    store.save(restored, target)
    again = store.resume(FakeEnv())
    assert len([m for m in again.messages if m.get("tool_call_id") == "stable-id"]) == 1
    record_property("recovery_expected", "consistent-no-replay")
    record_property("recovery_correct", True)


@pytest.mark.parametrize("corruption", ["manifest", "blob", "missing"])
def test_corrupt_latest_falls_back_previous_complete_generation(tmp_path, corruption):
    env, state, store = FakeEnv(), pending_state(), V4CheckpointStore(tmp_path)
    store.save(state, env)
    env.files["example.py"] = "VALUE = 2\n"
    store.save(state, env)
    path = sorted(store.directory.glob("generation-*.json"))[-1]
    target = (
        path
        if corruption == "manifest"
        else store.directory / json.loads(path.read_text())["workspace"]
    )
    if corruption == "missing":
        target.unlink()
    else:
        target.write_bytes(b"corrupted")
    restored_env = FakeEnv()
    restored = store.resume(restored_env)
    assert restored_env.files["example.py"] == "VALUE = 1\n"
    assert restored.metadata["v4"]["checkpoint_generation"] == 1 and store.load_events
    assert any(e["type"] == "checkpoint_error" for e in restored.events)


def test_retention_dedup_and_gc(tmp_path):
    env, state, store = FakeEnv(), pending_state(), V4CheckpointStore(tmp_path)
    for i in range(8):
        if i < 6:
            env.files["example.py"] = f"VALUE = {i}\n"
        store.save(state, env)
    assert len(list(store.directory.glob("generation-*.json"))) == 4
    assert len(list(store.directory.glob("workspace-*.json"))) == 2
    assert store.resume(FakeEnv()).budget == state.budget


@pytest.mark.parametrize("failure", ["oversize", "disk"])
def test_pre_dispatch_failure_prevents_side_effect(tmp_path, failure, record_property):
    env = FakeEnv()
    config = HarnessConfig.for_profile(
        "v4", checkpoint_max_bytes=1 if failure == "oversize" else 65536
    )
    agent = RepoFixAgent(
        env,
        "fix",
        artifact_dir(tmp_path) / "trace.jsonl",
        "",
        "fixture",
        config,
        FakeModelClient([{"calls": [("bash", {"command": "side-effect"})]}]),
    )
    if failure == "disk":
        with patch(
            "repofix.harness.checkpoint_v4.durable_write", side_effect=OSError("injected ENOSPC")
        ):
            result = agent.run()
    else:
        result = agent.run()
    assert result.status == "checkpoint_error" and "side-effect" not in env.executed
    assert "not executed" in agent.state.messages[-1]["content"]
    record_property("failure_injection", True)
    record_property("recovery_expected", "fail-closed")
    record_property("recovery_correct", True)


def test_lost_job_and_stale_evidence_restored_truthfully(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.env.argv_handler = scripted_pytest()
    call(runtime, "run_tests", {"targets": ["example.py"]})
    runtime.env.files["example.py"] = "VALUE = 9\n"
    runtime.state.metadata["v4"]["background_jobs"] = [{"id": "old", "state": "RUNNING"}]
    store = V4CheckpointStore(tmp_path / "checkpoint-run")
    store.save(runtime.state, runtime.env)
    target = FakeEnv()
    restored = store.resume(target)
    assert restored.metadata["v4"]["local_validation"]["state"] == "STALE"
    assert restored.metadata["v4"]["background_jobs"][0]["state"] == "LOST_AFTER_RESTART"
    assert not target.executed


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", ".git/config", ".env", "credentials"])
def test_unsafe_snapshot_rejected_before_restore(name):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        item = tarfile.TarInfo(name)
        item.size = 3
        tar.addfile(item, io.BytesIO(b"bad"))
    snapshot = {
        "kind": "docker",
        "patch": "",
        "untracked_tar": base64.b64encode(buffer.getvalue()).decode(),
    }
    with pytest.raises(ValueError, match="unsafe"):
        validate_snapshot(snapshot, 65536)


def test_secret_refused_and_no_valid_backup_is_clear(tmp_path):
    store = V4CheckpointStore(tmp_path, secrets=["fixture-secret-value"])
    with pytest.raises(ValueError, match="credential"):
        store.save(RunState(), FakeEnv({"a.py": "fixture-secret-value"}))
    with pytest.raises(ValueError, match="no complete valid V4"):
        store.load()
