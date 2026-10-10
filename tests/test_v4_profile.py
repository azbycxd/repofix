"""V4 opt-in contract; historical schemas/state remain unchanged."""

import hashlib
import json
from dataclasses import asdict
from unittest.mock import patch

import pytest

from repofix.agent import AgentConfig, RepoFixAgent
from repofix.cli import build_parser
from repofix.harness.config import HarnessConfig, V4_FIELDS, serialize_config
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.runtime import Runtime
from repofix.harness.state import RunState


def make_agent(tmp_path, profile="v4", script=()):
    return RepoFixAgent(
        FakeEnv(),
        "public issue",
        tmp_path / "trace.jsonl",
        "",
        "test",
        HarnessConfig.for_profile(profile),
        FakeModelClient(script),
    )


def test_profile_defaults_and_legacy_serialization():
    for profile in ("v1", "v3", "v4"):
        config = HarnessConfig.for_profile(profile)
        assert all(getattr(config, name) == (profile == "v4") for name in V4_FIELDS)
        assert set(serialize_config(config)).intersection(V4_FIELDS) == (
            V4_FIELDS if profile == "v4" else set()
        )
        assert config.checkpointing == (profile != "v1")
        assert config.subagents == "none"
    assert asdict(HarnessConfig.for_profile("v1").legacy_config()) == asdict(AgentConfig())
    with pytest.raises(ValueError, match="require profile v4"):
        HarnessConfig.for_profile("v3", structured_validation=True)
    with pytest.raises(ValueError, match="booleans"):
        HarnessConfig.for_profile("v4", progress_monitor="yes")
    assert not HarnessConfig.for_profile("v4", progress_monitor=False).progress_monitor


def test_v3_native_schema_hash_unchanged(tmp_path):
    # Recorded from untouched e9f3157 before adding profile=v4 (M0_baseline.json).
    agent = make_agent(tmp_path, "v3")
    schemas = Runtime(agent, RunState()).registry.schemas
    digest = hashlib.sha256(
        json.dumps(schemas, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert digest == "6905a43242cbe54f879c1923d7a2af7e8503b71031529a5aad7e0a089ecb3c17"


def test_v4_reuses_the_existing_loop(tmp_path):
    agent = make_agent(tmp_path)
    with patch("repofix.harness.loop.run_v3", return_value="shared-loop") as loop:
        assert agent.run() == "shared-loop"
    loop.assert_called_once_with(agent)


def test_v4_cli_and_terminal_semantics(tmp_path):
    args = build_parser().parse_args(["--repo", str(tmp_path), "--issue", "fix", "--profile", "v4"])
    assert args.profile == "v4"
    agent = make_agent(tmp_path, script=[{"content": "no tool"}, {"content": "no tool again"}])
    result = agent.run()
    assert result.status == "no_tool_call" and not result.submitted
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["termination_reason"] == "no_tool_call"


def test_legacy_state_roundtrip_has_no_new_v4_fields():
    original = RunState(messages=[{"role": "user", "content": "old"}])
    payload = asdict(original)
    assert not any(key.startswith("v4") for key in payload)
    restored = RunState(**{key: value for key, value in payload.items() if key != "budget"})
    assert asdict(restored) == payload
