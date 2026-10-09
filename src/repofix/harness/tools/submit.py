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
                self.state.last_validation = {
                    "command": "verify subagent",
                    "output": verified["evidence"],
                    "exit_code": 0,
                }
                self.state.last_validation_version = self.state.workspace_version
            else:
                self.state.metadata["submit_forced"] = True
                self.state.submit_blocks = 3
        if self.hooks:
            decision = self.hooks.pre_submit()
            if isinstance(decision, (Block, Deny)):
                return ToolResult(decision.reason, metadata={"hook_blocked": True})
        self.state.submitted = True
        self.state.termination = (
            "submit_forced" if self.state.metadata.get("submit_forced") else "submitted"
        )
        return ToolResult("Submission accepted.")
