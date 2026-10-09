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
        from .workspace import RepoFiles
        self.files = RepoFiles(self.env)
        from .tools.shell import JobManager
        self.jobs = JobManager(self.env)
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
        if self.config.apply_patch_enabled:
            self.registry.add(ToolSpec("apply_patch", schema("apply_patch", "Apply an atomic multi-file text patch in /testbed; Python syntax failures roll back all files.",
                {"patch": {"type": "string"}}, ["patch"]), False, self.apply_patch))
        if self.config.plan_tool:
            from .tools.plan import update_plan
            self.registry.add(ToolSpec("update_plan", schema("update_plan", "Record task steps; at most one in_progress. This is optional, not a submit gate.",
                {"steps": {"type": "array", "items": {"type": "object", "properties": {
                    "step": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
                    "required": ["step", "status"], "additionalProperties": False}}}, ["steps"]), False,
                lambda args: ToolResult(update_plan(self.state, args["steps"]))))
        if self.config.background_shell:
            self.registry.specs["bash"] = ToolSpec("bash", schema("bash", "Run a command in /testbed; timeout leaves a background job alive.",
                {"command": {"type": "string"}, "timeout": {"type": "integer", "default": 120, "maximum": 600},
                 "run_in_background": {"type": "boolean", "default": False}}, ["command"]), False, self.bash)
            self.registry.add(ToolSpec("job_output", schema("job_output", "Read background command output and status.",
                {"job_id": {"type": "string"}, "tail_lines": {"type": "integer", "default": 100}}, ["job_id"]), True, self.job_output))
            self.registry.add(ToolSpec("job_kill", schema("job_kill", "Stop a background job process group.",
                {"job_id": {"type": "string"}}, ["job_id"]), False,
                lambda args: self.shell_result(self.jobs.kill(args["job_id"]))))

    def bash(self, args):
        from .tools.shell import foreground
        command, timeout = args["command"], args.get("timeout", 120)
        if not isinstance(command, str) or not command.strip() or type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("command required; timeout must be an integer in 1..600")
        if self.config.background_shell:
            job_id = self.jobs.start(command)
            data = self.jobs.output(job_id) if args.get("run_in_background", False) else self.jobs.wait(job_id, timeout)
        else:
            data = foreground(self.env, command, timeout)
        return self.shell_result(data)

    @staticmethod
    def shell_result(data):
        return ToolResult(data["output"], data["exit_code"], {"shell": True, **{k: v for k, v in data.items() if k != "output"}})

    def job_output(self, args):
        return self.shell_result(self.jobs.output(args["job_id"], args.get("tail_lines", 100)))

    def view(self, args):
        import hashlib
        from repofix.env import DockerEnv
        path = self.files.resolve(args["path"])
        text = self.files.read(path)
        start, end = args["start_line"], args["end_line"]
        if type(start) is not int or type(end) is not int:
            raise ValueError("line bounds must be integers")
        content = f"/testbed/{path}\n" + DockerEnv._numbered_lines(text, start, end)
        self.state.file_reads[path] = hashlib.sha256(text.encode()).hexdigest()
        return ToolResult(content)

    def read_guard(self, path, content):
        import hashlib
        if self.config.read_before_edit and self.state.file_reads.get(path) != hashlib.sha256(content.encode()).hexdigest():
            raise ValueError(f"view {path} before editing: never read or content changed since view")

    def replace(self, args):
        path = self.files.resolve(args["path"])
        self.read_guard(path, self.files.read(path))
        result = self.env.str_replace_file(**args)
        return ToolResult(result.output, metadata={"str_replace_failures": int(not result.success),
                                                   "syntax_rollbacks": int(result.syntax_rollback)})

    def apply_patch(self, args):
        from .tools.patch import prepare_patch
        updates = prepare_patch(args["patch"], self.files, self.read_guard)
        try:
            self.files.apply(updates)
        except (SyntaxError, RuntimeError) as exc:
            return ToolResult(f"apply_patch failed; all files restored: {exc}", metadata={"syntax_rollbacks": 1, "error": True})
        return ToolResult("Patch applied:\n" + "\n".join(updates))

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
        if result.metadata.get("shell"):
            from .tools.shell import benign_exit
            if result.metadata.get("still_running"):
                header = f"still running: job_id={result.metadata['job_id']}"
            else:
                header = f"exit_code: {result.exit_code} | duration: {result.metadata.get('duration', 0):.3f}s | truncated: {'yes' if observation.truncated else 'no'}"
                if benign_exit(result.metadata.get("command", ""), result.exit_code):
                    header += " | benign_exit"
                    result.metadata["benign_exit"] = True
            result.content = header + "\n" + result.content
        result.metadata.update({key: value for key, value in asdict(observation).items() if key != "content"})
        result.metadata["duration_seconds"] = time.monotonic() - started
        result.metadata["returned_chars"] = len(result.content)
        return result
