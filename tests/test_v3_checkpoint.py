import json

import pytest

from repofix.agent import RepoFixAgent
from repofix.harness.checkpoint import CheckpointStore
from repofix.harness.config import HarnessConfig
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.resume import resume_run
from repofix.harness.state import RunState


def test_no_tool_call_is_terminal_and_not_resumable(tmp_path):
    agent = RepoFixAgent(
        FakeEnv(),
        "fix",
        tmp_path / "t.jsonl",
        "",
        "test",
        HarnessConfig.for_profile("v3"),
        FakeModelClient([{}, {}]),
    )
    assert agent.run().status == "no_tool_call"
    with pytest.raises(ValueError, match="no_tool_call"):
        CheckpointStore(tmp_path).resume(FakeEnv())
    with pytest.raises(ValueError, match="no_tool_call"):
        resume_run(tmp_path, "")


def test_snapshot_pending_restore_and_corrupt_fallback(tmp_path):
    env, state = FakeEnv(), RunState(step=1)
    state.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
    state.pending_calls = [{"id": "c", "name": "bash", "arguments": "{}"}]
    state.file_reads = {"example.py": "hash"}
    state.budget.estimated_cost = 0.1
    env.files["example.py"] = "VALUE = 2\n"
    env.files["untracked.txt"] = "keep"
    store = CheckpointStore(tmp_path)
    store.save(state, env)
    (tmp_path / "checkpoints/step-0002.json").write_text("broken")
    target = FakeEnv()
    loaded = store.resume(target)
    assert target.files == env.files
    assert loaded.step == 1 and loaded.budget.estimated_cost == 0.1
    assert loaded.file_reads == state.file_reads
    assert (
        loaded.messages[-1]["tool_call_id"] == "c"
        and "interrupted" in loaded.messages[-1]["content"]
    )
    assert not loaded.pending_calls and not target.executed


def test_complete_step_resume_matches_uninterrupted(tmp_path):
    script = [
        {"calls": [("view", {"path": "example.py", "start_line": 1, "end_line": 1})]},
        {"calls": [("submit", {})]},
    ]
    config = HarnessConfig.for_profile("v3", hooks_enabled=False)
    first = RepoFixAgent(
        FakeEnv(),
        "fix",
        tmp_path / "a/t.jsonl",
        "",
        "test",
        config,
        FakeModelClient([script[0], KeyboardInterrupt()]),
    )
    first.run()
    resumed = RepoFixAgent(
        FakeEnv(), "fix", tmp_path / "a/t.jsonl", "", "test", config, FakeModelClient([script[1]])
    )
    resumed.state = CheckpointStore(tmp_path / "a").resume(resumed.env)
    resumed.run()
    full = RepoFixAgent(
        FakeEnv(), "fix", tmp_path / "b/t.jsonl", "", "test", config, FakeModelClient(script)
    )
    full.run()

    # Provider call IDs are provider-specific; normalize those only.
    def normalized(messages):
        text = (
            json.dumps(messages).replace("call_2_0", "submit-id").replace("call_1_0", "submit-id")
        )
        return json.loads(text)

    assert normalized(resumed.state.messages) == normalized(full.state.messages)
    assert resumed.state.budget == full.state.budget


def test_single_bash_interruption_preserves_pending_call_without_replay(tmp_path):
    command = "interrupted-command"

    def interrupted(env):
        env.files["example.py"] = "VALUE = 99\n"
        raise KeyboardInterrupt()

    config = HarnessConfig.for_profile("v3", hooks_enabled=False)
    env = FakeEnv(commands={command: interrupted})
    first = RepoFixAgent(
        env,
        "fix",
        tmp_path / "t.jsonl",
        "",
        "test",
        config,
        FakeModelClient([{"calls": [("bash", {"command": command})]}]),
    )
    with pytest.raises(KeyboardInterrupt):
        first.run()
    assert env.executed.count(command) == 1
    saved, _ = CheckpointStore(tmp_path).load()
    assert saved.step == 1 and saved.budget.provider_calls == 1
    assert len(saved.pending_calls) == 1
    pending_id = saved.pending_calls[0]["id"]
    assert saved.messages[-1]["tool_calls"][0]["id"] == pending_id

    target = FakeEnv(commands={command: AssertionError("must not replay interrupted bash")})
    state = CheckpointStore(tmp_path).resume(target)
    assert target.files == target.baseline  # restore pre-tool snapshot
    assert not target.executed and not state.pending_calls
    interrupted_result = {
        "role": "tool",
        "tool_call_id": pending_id,
        "content": "[interrupted: tool call did not complete before the run stopped]",
    }
    assert state.messages[-1] == interrupted_result
    client = FakeModelClient([{"calls": [("submit", {})]}])
    resumed = RepoFixAgent(target, "fix", tmp_path / "t.jsonl", "", "test", config, client)
    resumed.state = state
    result = resumed.run()
    assert result.submitted and result.provider_calls == 2
    assert client.requests[0]["messages"][-1] == interrupted_result
    assert command not in target.executed
