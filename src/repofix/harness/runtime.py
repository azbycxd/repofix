"""V3 tool assembly and observation formatting, independent of model orchestration."""
import json
import time
from dataclasses import dataclass, field, asdict

from repofix.agent import TOOLS, format_tool_observation, parse_tool_arguments
from repofix.search import BM25Index, format_search_results
from .tools.files import grep
from .tools.registry import ToolRegistry, ToolSpec, schema


@dataclass
class ToolResult:
    content: str
    exit_code: int | None = None
    metadata: dict = field(default_factory=dict)


class Runtime:
    def __init__(self, agent, state):
        self.agent, self.state = agent, state
        self.env, self.config = agent.env, agent.config
        from .hooks import HookEngine
        self.hooks = HookEngine(self.env, self.config, state) if self.config.hooks_enabled else None
        self.index, self.index_stats = BM25Index.from_repository(self.env)
        handlers = {"bash": self.bash, "view": self.view, "str_replace": self.replace,
                    "search_code": self.search, "submit": self.submit}
        self.registry = ToolRegistry(ToolSpec(item["function"]["name"], item,
            item["function"]["name"] in {"view", "search_code"}, handlers[item["function"]["name"]]) for item in TOOLS)
        self.registry.add(ToolSpec("grep", schema("grep", "Search repository text; return file and line matches.",
            {"pattern": {"type": "string"}, "path_glob": {"type": "string"},
             "max_results": {"type": "integer", "default": 50}}, ["pattern"]), True,
             lambda args: ToolResult(grep(self.env, **args))))

    def bash(self, args):
        result = self.env.execute(args["command"], self.config.tool_timeout_seconds)
        return ToolResult(result.output + ("\n[command timed out]" if result.timed_out else f"\n[exit_code={result.exit_code}]"),
                          result.exit_code, {"command": args["command"], "timed_out": result.timed_out})

    def view(self, args):
        return ToolResult(self.env.view_file(**args)) if hasattr(self.env, "v3_view") else ToolResult(
            self.env.view_file(args["path"], args["start_line"], args["end_line"]))

    def replace(self, args):
        result = self.env.str_replace_file(**args)
        return ToolResult(result.output, metadata={"str_replace_failures": int(not result.success),
                                                   "syntax_rollbacks": int(result.syntax_rollback)})

    def search(self, args):
        query, top_k = args.get("query"), args.get("top_k", 5)
        if not isinstance(query, str) or not query.strip() or type(top_k) is not int or not 1 <= top_k <= 20:
            raise ValueError("query must be nonempty and top_k must be in 1..20")
        return ToolResult(format_search_results(self.index.search(query, top_k=top_k)))

    def submit(self, args):
        if self.hooks:
            from .hooks import Block, Deny
            decision = self.hooks.pre_submit()
            if isinstance(decision, (Block, Deny)):
                return ToolResult(decision.reason, metadata={"hook_blocked": True})
        self.state.submitted = True
        self.state.termination = "submit_forced" if self.state.metadata.get("submit_forced") else "submitted"
        return ToolResult("Submission accepted.")

    def execute(self, call):
        started = time.monotonic()
        args, error = parse_tool_arguments(call["arguments"])
        try:
            if error:
                raise ValueError(error)
            hooked = {**call, "args": args}
            if self.hooks:
                from .hooks import Block, Deny
                decision = self.hooks.pre(hooked)
                if isinstance(decision, (Block, Deny)):
                    return ToolResult(decision.reason, metadata={"hook_blocked": True})
                before = self.hooks.before()
            result = self.registry.execute(call["name"], hooked["args"])
            if self.hooks:
                result = self.hooks.post(hooked, result, before)
        except Exception as exc:
            result = ToolResult(f"{call['name']} error: {exc}", metadata={"error": True})
        path = None
        if len(result.content) > self.config.tool_output_max_chars:
            # Call IDs are provider-controlled. Never use them as filesystem paths.
            import hashlib
            key = hashlib.sha256(call["id"].encode()).hexdigest()[:16]
            path = f"/tmp/repofix_out_{self.state.step}_{key}.txt"
            self.env.write_text_file(path, result.content)
        observation = format_tool_observation(result.content, "", path,
            self.config.tool_output_max_chars, self.config.tool_output_head_chars,
            self.config.tool_output_tail_chars)
        result.content = observation.content
        result.metadata.update({key: value for key, value in asdict(observation).items() if key != "content"})
        result.metadata["duration_seconds"] = time.monotonic() - started
        return result
