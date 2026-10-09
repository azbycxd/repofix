import time
from repofix.harness.tools.registry import ToolRegistry, ToolSpec, schedule
from repofix.harness.tools.files import grep
from repofix.harness.fake import FakeEnv


def test_readonly_parallel_order_and_write_barriers():
    events = []
    def execute(call):
        events.append("start" + call["id"])
        time.sleep(call.get("sleep", 0))
        events.append("end" + call["id"])
        return call["id"]
    registry = ToolRegistry([ToolSpec("view", {}, True, None), ToolSpec("edit", {}, False, None)])
    calls = [{"id": "1", "name": "view", "sleep": .15}, {"id": "2", "name": "view", "sleep": .15},
             {"id": "3", "name": "edit"}, {"id": "4", "name": "view"}]
    start = time.monotonic()
    output = list(schedule(calls, registry, execute, parallel=True))
    assert time.monotonic() - start < .28
    assert [result for _, result, _ in output] == ["1", "2", "3", "4"]
    assert events.index("start3") > max(events.index("end1"), events.index("end2"))
    assert events.index("start4") > events.index("end3")


def test_grep_limits_and_glob():
    env = FakeEnv({"a.py": "word\nword\n", "a.txt": "word\n"})
    assert grep(env, "word", "*.py", 1) == "a.py:1:word"


def test_v3_fake_loop(tmp_path):
    from repofix.agent import RepoFixAgent
    from repofix.harness.config import HarnessConfig
    from repofix.harness.model import FakeModelClient
    client = FakeModelClient([{"calls": [("grep", {"pattern": "VALUE"})]}, {"calls": [("submit", {})]}])
    result = RepoFixAgent(FakeEnv(), "issue", tmp_path / "trace.jsonl", "", "test",
        HarnessConfig.for_profile("v3", hooks_enabled=False), client).run()
    assert result.submitted and result.tool_calls == 2
