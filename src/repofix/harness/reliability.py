"""V4-only trace envelope. Producers and independent verdict boundaries stay explicit."""

import hashlib
import json
import re
from pathlib import Path

from repofix.core import TrajectoryWriter

from .config import serialize_config
from .model import FakeModelClient


class ReliabilityTrace(TrajectoryWriter):
    def __init__(self, agent, state):
        super().__init__(agent.trace.path, agent.trace.secrets)
        self.agent, self.state = agent, state
        self.sequence = sum(1 for _ in self.path.open()) if self.path.exists() else 0
        self.config_hash = hashlib.sha256(
            json.dumps(serialize_config(agent.config), sort_keys=True).encode()
        ).hexdigest()

    def _redact_text(self, value):
        value = super()._redact_text(value)
        value = re.sub(
            r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED_JWT]", value
        )
        root = Path(__file__).resolve().parents[3]
        return value.replace(str(root), "$WORKTREE").replace(str(Path.home()), "$HOME")

    def write(self, record):
        self.sequence += 1
        facts = self.state.metadata.get("v4", {})
        validation = facts.get("local_validation", {})
        progress = facts.get("progress", {})
        env = self.agent.env
        backend = (
            "fake"
            if hasattr(env, "files")
            else "docker"
            if getattr(env, "container", None)
            else "local-subprocess"
        )
        model_backend = (
            "fake"
            if isinstance(self.agent.client, FakeModelClient)
            else "provider"
            if type(self.agent.client).__module__.startswith("openai")
            else "test-double"
        )
        enriched = {
            "schema_version": 4,
            "producer": "harness",
            "sequence": self.sequence,
            "run_id": getattr(env, "run_id", self.path.parent.name),
            "task_id": getattr(env, "instance_id", None),
            "code_sha": self.agent.git_commit,
            "config_hash": self.config_hash,
            "model": self.agent.config.model,
            "execution_backend": backend,
            "model_backend": model_backend,
            "step": self.state.step,
            "tool_call_id": None,
            "tool_name": None,
            "execution_state": None,
            "workspace_signature": validation.get("workspace_signature"),
            "validation_evidence_id": validation.get("evidence_id"),
            "validation_state": validation.get("state", "UNVERIFIED"),
            "submission_state": self.state.termination,
            "judge_verdict": "NOT_JUDGED",
            "progress_signal": facts.get("last_progress_signal"),
            "repeat_count": progress.get("repeat_count"),
            "nudge_count": int(self.state.metadata.get("nudge_used", False)),
            "replan_count": progress.get("replans"),
            "checkpoint_generation": facts.get("checkpoint_generation"),
            "snapshot_bytes": None,
            "checkpoint_duration": None,
            **record,
        }
        if isinstance(enriched.get("tool_calls"), list):
            producer = "subagent" if self.state.depth else "assistant"
            enriched["tool_calls"] = [
                {**call, "producer": producer} for call in enriched["tool_calls"]
            ]
        if "events" in enriched:
            enriched["events"] = [{"producer": "harness", **event} for event in enriched["events"]]
        if record.get("type") == "validation":
            evidence = record["evidence"]
            enriched.update(
                validation_evidence_id=evidence["id"],
                workspace_signature=evidence["workspace_signature_after"],
                validation_state="LOCAL_TESTS_PASSED"
                if evidence["outcome"] == "PASS"
                else evidence["outcome"],
            )
        super().write(enriched)


def configure_trace(agent, state):
    if agent.config.reliability_telemetry and not isinstance(agent.trace, ReliabilityTrace):
        agent.trace = ReliabilityTrace(agent, state)
