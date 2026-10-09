"""Fixture recorded from 3fe3c0e BEFORE moving the legacy loop."""
import json
from dataclasses import asdict
from pathlib import Path

from repofix.agent import RepoFixAgent
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.config import HarnessConfig


def golden_run(tmp_path):
    model = FakeModelClient([
        {"calls": [("view", {"path": "example.py", "start_line": 1, "end_line": 2})]},
        {"calls": [("str_replace", {"path": "example.py", "old_str": "1", "new_str": "2"})]},
        {"calls": [("bash", {"command": "long-output"})]},
        {"calls": [("search_code", {"query": "VALUE", "top_k": 1})]},
        {"calls": [("submit", {})]},
    ])
    env = FakeEnv(commands={"long-output": ("x" * 12010, 0, False, 0)})
    result = RepoFixAgent(env, "Set VALUE to 2.", tmp_path / "trace.jsonl", "", "fixture", client=model,
                         config=HarnessConfig.for_profile("v1")).run()
    summary = asdict(result)
    for key in ("wall_time_seconds", "index_build_seconds", "trajectory_path"):
        summary.pop(key)
    return {"requests": model.requests, "result": summary}


def test_v1_golden(tmp_path):
    expected = json.loads((Path(__file__).parent / "fixtures/v1_golden.json").read_text())
    assert golden_run(tmp_path) == expected


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        content = golden_run(Path(directory))
    target = Path(__file__).parent / "fixtures/v1_golden.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(content, indent=2) + "\n")
