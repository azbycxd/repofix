import json

from ..hooks import Block, Deny
from ..subagent import run_subagent
from .result import ToolResult


class SubmitTool:
    def __init__(self, runtime):
        self.runtime = runtime
        self.state, self.config, self.hooks = runtime.state, runtime.config, runtime.hooks

    def submit(self, args):
        if (
            self.config.verify_on_submit
            and self.config.subagents in {"verify", "both"}
            and self.state.depth == 0
        ):
            rounds = self.state.metadata.get("verify_submit_rounds", 0)
            if rounds < 2:
                self.state.metadata["verify_submit_rounds"] = rounds + 1
                verified = run_subagent(
                    self.runtime, "verify", "Check the final patch before submission."
                )
                if verified["verdict"] == "FAIL":
                    self.state.count("hook_blocks")
                    return ToolResult(json.dumps(verified), metadata={"hook_blocked": True})
                if not self.config.structured_validation:
                    self.state.last_validation = {
                        "command": "verify subagent",
                        "output": verified["evidence"],
                        "exit_code": 0,
                    }
                    self.state.last_validation_version = self.state.workspace_version
                else:
                    self.state.events.append(
                        {
                            "type": "verify_advice",
                            "producer": "subagent",
                            "verdict": verified["verdict"],
                            "trusted_validation": False,
                        }
                    )
            else:
                self.state.metadata["submit_forced"] = True
                self.state.submit_blocks = 3
        if self.runtime.evidence_policy:
            reason = self.runtime.evidence_policy.before_submit()
            if reason:
                return ToolResult(reason, metadata={"hook_blocked": True, "producer": "harness"})
        if self.hooks:
            decision = self.hooks.pre_submit()
            if isinstance(decision, (Block, Deny)):
                return ToolResult(decision.reason, metadata={"hook_blocked": True})
        self.state.submitted = True
        self.state.termination = (
            "submit_forced" if self.state.metadata.get("submit_forced") else "submitted"
        )
        if self.runtime.evidence_policy:
            local = self.state.metadata["v4"]["local_validation"]
            if self.state.metadata.get("submit_forced"):
                local["state"] = "UNVERIFIED"
            return ToolResult(
                "Candidate patch accepted; local_validation="
                + local["state"]
                + "; task acceptance requires an independent Judge."
            )
        return ToolResult("Submission accepted.")
