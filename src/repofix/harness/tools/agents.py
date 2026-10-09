import json

from ..subagent import run_subagent
from .result import ToolResult


class AgentTools:
    def __init__(self, runtime):
        self.runtime = runtime
        self.agent, self.state = runtime.agent, runtime.state

    def explore(self, args):
        return ToolResult(
            run_subagent(
                self.runtime, "explore", args["question"], args.get("thoroughness", "medium")
            )
        )

    def verify(self, args):
        return ToolResult(
            json.dumps(
                run_subagent(self.runtime, "verify", args.get("focus", "Check the current diff.")),
                ensure_ascii=False,
            )
        )

    def report(self, args):
        if self.agent.subagent_mode == "explore":
            if not isinstance(args.get("summary"), str) or len(args["summary"]) > 1500:
                raise ValueError("summary must be text <= 1500 characters")
            self.state.metadata["report"] = args["summary"]
        else:
            if args.get("verdict") not in {"PASS", "FAIL"} or not isinstance(
                args.get("evidence"), str
            ):
                raise ValueError("report requires PASS/FAIL and evidence")
            self.state.metadata["report"] = args
        self.state.submitted = True
        self.state.termination = "submitted"
        return ToolResult("Report accepted.")
