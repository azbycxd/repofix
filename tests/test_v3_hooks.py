import sys
from repofix.harness.hooks import Allow, Block, Deny, Rewrite, ExternalHook, HookEngine, verify_before_submit
from repofix.harness.config import HarnessConfig
from repofix.harness.state import RunState
from repofix.harness.fake import FakeEnv
from repofix.harness.runtime import ToolResult


def test_external_protocol():
    state = RunState()
    deny = ExternalHook([sys.executable, "-c", "import sys; sys.stderr.write('denied'); sys.exit(2)"])({}, state)
    assert isinstance(deny, Deny) and deny.reason == "denied"
    assert isinstance(ExternalHook([sys.executable, "-c", "pass"])({}, state), Allow)
    rewrite = ExternalHook([sys.executable, "-c", "print('{\"rewrite\": {\"x\": 1}}')"])({}, state)
    assert isinstance(rewrite, Rewrite) and rewrite.args == {"x": 1}


def test_syntax_catches_bash_and_repeat_edits():
    env, state = FakeEnv(), RunState()
    hooks = HookEngine(env, HarnessConfig.for_profile("v3"), state)
    for value in ("VALUE = (", "VALUE = )"):
        before = hooks.before()
        env.files["example.py"] = value
        result = hooks.post({"name": "bash"}, ToolResult("changed"), before)
        assert "syntax_check" in result.content
        assert env.files["example.py"] == value  # post hook does not roll back
    assert state.workspace_version == 2


def test_validation_must_follow_last_edit_and_block_cap():
    state, env = RunState(), FakeEnv()
    hooks = HookEngine(env, HarnessConfig.for_profile("v3"), state)
    assert isinstance(verify_before_submit(state), Block)
    before = hooks.before()
    hooks.post({"name": "bash"}, ToolResult("passed", 0, {"command": "python -m pytest"}), before)
    assert isinstance(verify_before_submit(state), Allow)
    before = hooks.before()
    env.files["example.py"] = "VALUE = 3\n"
    hooks.post({"name": "bash"}, ToolResult("edited"), before)
    assert isinstance(verify_before_submit(state), Block)
    assert isinstance(verify_before_submit(state), Block)
    assert isinstance(verify_before_submit(state), Allow)
    assert state.metadata["submit_forced"]


def test_rewrite_callable_and_policy_no_rules():
    h = HookEngine(FakeEnv(), HarnessConfig.for_profile("v3"), RunState(),
        pre=[lambda call, state: Rewrite({"command": "pytest"})])
    call = {"name": "bash", "args": {"command": "old"}}
    assert isinstance(h.pre(call), Allow)
    assert call["args"]["command"] == "pytest"


def test_post_and_submit_external_events():
    hook = ExternalHook([sys.executable, "-c", "import json,sys; x=json.load(sys.stdin); print(json.dumps({'content': x['event']}))"])
    result = hook({"name": "bash"}, ToolResult("old"), RunState())
    assert result.content == "PostToolUse"
    assert isinstance(ExternalHook([sys.executable, "-c", "import sys; sys.exit(2)"])(RunState()), Deny)


def test_rewrite_cannot_bypass_policy_and_echo_is_not_verification():
    from repofix.harness.hooks import is_validation_command
    h = HookEngine(FakeEnv(), HarnessConfig.for_profile("v3"), RunState(),
        pre=[lambda c, s: Rewrite({"command": "curl example"})])
    assert isinstance(h.pre({"name": "bash", "args": {"command": "echo safe"}}), Deny)
    assert not is_validation_command("echo 'pytest'")
    assert is_validation_command("cd /testbed && python -m pytest")


def test_post_hook_failure_preserves_tool_result():
    env = FakeEnv()
    hook = ExternalHook([sys.executable, "-c", "import sys; sys.stderr.write('broken hook'); sys.exit(1)"])
    h = HookEngine(env, HarnessConfig.for_profile("v3"), RunState(), post=[hook])
    result = h.post({"name": "bash"}, ToolResult("actual command output"), h.before())
    assert "actual command output" in result.content and "broken hook" in result.content


def test_registered_python_hooks_run_in_real_loop_and_force_after_three(tmp_path):
    from repofix.agent import RepoFixAgent
    from repofix.harness.model import FakeModelClient
    client = FakeModelClient([
        {"calls": [("bash", {"command": "echo denied"})]},
        *[{"calls": [("submit", {})]} for _ in range(4)],
    ])
    env = FakeEnv()
    agent = RepoFixAgent(env, "fix", tmp_path / "t", "", "test",
                        HarnessConfig.for_profile("v3"), client)
    agent.register_hook("PreToolUse", lambda call, state:
                        Deny("custom policy refusal") if call["name"] == "bash" else Allow())
    agent.register_hook("PostToolUse", lambda call, result, state: result)
    result = agent.run()
    assert "custom policy refusal" in client.requests[1]["messages"][-1]["content"]
    assert "echo denied" not in env.executed
    assert result.status == "submit_forced" and agent.state.submit_blocks == 3
