import json
from repofix.harness.context import ContextManager, estimate, recent_complete_messages
from repofix.harness.config import HarnessConfig
from repofix.harness.state import RunState
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient


def state_with_outputs(size=2000, count=5):
    state = RunState(messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "task"}])
    for i in range(count):
        state.messages.extend([{"role": "assistant", "tool_calls": [{"id": str(i), "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
                               {"role": "tool", "tool_call_id": str(i), "content": "x" * size}])
    return state


def test_mask_only_and_usage_anchor(tmp_path):
    state = state_with_outputs()
    state.metadata["usage_anchor"] = {"tokens": 10000, "messages": len(state.messages)}
    assert estimate(state) >= 10000
    config = HarnessConfig.for_profile("v3", context_window=7000, keep_recent_tool_results=1)
    client = FakeModelClient([])
    event = ContextManager(config, client, FakeEnv(), tmp_path).maybe_compact(state)
    assert event["masked_results"] == 4 and not event["summary_called"]
    assert len(list((tmp_path / "context").glob("*.txt"))) == 4
    assert state.messages[3]["tool_call_id"] == "0"
    assert not client.requests


def test_summary_authoritative_fields_and_protocol(tmp_path):
    state = state_with_outputs(size=5000)
    state.plan = [{"step": "fix", "status": "in_progress"}]
    state.last_validation = {"command": "pytest", "exit_code": 0, "output": "passed"}
    env = FakeEnv(); env.files["example.py"] = "VALUE = 2\n"
    data = dict(goal="fix", constraints=[], done=[], verified_facts=[], failed_attempts=[], next_steps=[], files_changed=["lie"])
    client = FakeModelClient([{"content": json.dumps(data)}])
    config = HarnessConfig.for_profile("v3", context_window=2500, keep_recent_tool_results=1)
    event = ContextManager(config, client, env, tmp_path).maybe_compact(state)
    assert event["summary_called"]
    summary = json.loads(state.messages[2]["content"].split("\n", 1)[1])
    assert summary["files_changed"] == ["example.py"]
    assert summary["plan"] == state.plan and summary["task"] == "task"
    assert summary["last_validation"]["command"] == "pytest"
    assert state.messages[3]["role"] == "assistant" and state.messages[4]["role"] == "tool"
    assert state.budget.provider_calls == 1


def test_debounce_and_context_exhaustion(tmp_path):
    state = state_with_outputs()
    client = FakeModelClient([{"content": "invalid"}] * 3)
    config = HarnessConfig.for_profile("v3", context_window=1)
    manager = ContextManager(config, client, FakeEnv(), tmp_path)
    manager.maybe_compact(state)
    state.step = 1
    assert manager.maybe_compact(state) is None
    state.step = 2; manager.maybe_compact(state)
    state.step = 4; manager.maybe_compact(state)
    assert state.termination == "context_exhausted" and len(client.requests) == 3
