import pytest

from repofix.agent import TrajectoryWriter
from repofix.harness.checkpoint import CheckpointStore
from repofix.harness.fake import FakeEnv
from repofix.harness.state import RunState


def test_trace_checkpoint_redaction_and_snapshot_refusal(tmp_path):
    sentinel = "fixture-credential-never-real"
    writer = TrajectoryWriter(tmp_path / "t.jsonl", [sentinel])
    writer.write({"observation": sentinel, "nested": [sentinel]})
    state = RunState(messages=[{"role": "user", "content": sentinel}])
    store = CheckpointStore(tmp_path, writer._redact, writer.secrets)
    store.save(state, FakeEnv())
    assert sentinel not in writer.path.read_text()
    assert sentinel not in (tmp_path / "checkpoints/step-0000.json").read_text()
    with pytest.raises(ValueError, match="credential"):
        store.save(state, FakeEnv({"example.py": sentinel}))
