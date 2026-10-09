import json
import pytest
from repofix.harness.checkpoint import CheckpointStore
from repofix.harness.fake import FakeEnv
from repofix.harness.state import RunState


def test_no_tool_call_is_terminal_and_not_resumable(tmp_path):
    from repofix.agent import RepoFixAgent
    from repofix.harness.config import HarnessConfig
    from repofix.harness.model import FakeModelClient
    from repofix.harness.resume import resume_run
    agent = RepoFixAgent(FakeEnv(), "fix", tmp_path / "t.jsonl", "", "test",
                        HarnessConfig.for_profile("v3"), FakeModelClient([{}, {}]))
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
    state.budget.estimated_cost = .1
    env.files["example.py"] = "VALUE = 2\n"
    env.files["untracked.txt"] = "keep"
    store = CheckpointStore(tmp_path)
    store.save(state, env)
    (tmp_path / "checkpoints/step-0002.json").write_text("broken")
    target = FakeEnv()
    loaded = store.resume(target)
    assert target.files == env.files
    assert loaded.step == 1 and loaded.budget.estimated_cost == .1
    assert loaded.file_reads == state.file_reads
    assert loaded.messages[-1]["tool_call_id"] == "c" and "interrupted" in loaded.messages[-1]["content"]
    assert not loaded.pending_calls and not target.executed


def test_complete_step_resume_matches_uninterrupted(tmp_path):
    from repofix.agent import RepoFixAgent
    from repofix.harness.config import HarnessConfig
    from repofix.harness.model import FakeModelClient
    script = [{"calls": [("view", {"path": "example.py", "start_line": 1, "end_line": 1})]},
              {"calls": [("submit", {})]}]
    config = HarnessConfig.for_profile("v3", hooks_enabled=False)
    first = RepoFixAgent(FakeEnv(), "fix", tmp_path / "a/t.jsonl", "", "test", config,
                        FakeModelClient([script[0], KeyboardInterrupt()]))
    first.run()
    resumed = RepoFixAgent(FakeEnv(), "fix", tmp_path / "a/t.jsonl", "", "test", config, FakeModelClient([script[1]]))
    resumed.state = CheckpointStore(tmp_path / "a").resume(resumed.env)
    resumed.run()
    full = RepoFixAgent(FakeEnv(), "fix", tmp_path / "b/t.jsonl", "", "test", config, FakeModelClient(script))
    full.run()
    # Provider call IDs are provider-specific; normalize those only.
    def normalized(messages):
        text = json.dumps(messages).replace("call_2_0", "submit-id").replace("call_1_0", "submit-id")
        return json.loads(text)
    assert normalized(resumed.state.messages) == normalized(full.state.messages)
    assert resumed.state.budget == full.state.budget
