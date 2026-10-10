"""V3 tool assembly and observation formatting, independent of model orchestration."""

import hashlib
import json
import time
from dataclasses import asdict

from repofix.core import TOOLS, format_tool_observation, parse_tool_arguments
from repofix.search import BM25Index

from .hooks import Block, Deny, HookEngine, command_policy, is_validation_command
from .deadline import Deadline, TaskDeadline
from .progress import digest, normalize_observation
from .tools.agents import AgentTools
from .tools.files import FileTools, grep
from .tools.plan import update_plan
from .tools.registry import ToolRegistry, ToolSpec, schema
from .tools.result import ToolResult as ToolResult
from .tools.search import SearchTools
from .tools.shell import ShellTools, benign_exit
from .tools.submit import SubmitTool
from .validation import EvidencePolicy, ValidationRunner
from .workspace import RepoFiles


class Runtime:
    def __init__(self, agent, state):
        self.agent, self.state = agent, state
        self.env, self.config = agent.env, agent.config
        self.deadline = Deadline(self.config, state) if self.config.profile == "v4" else None
        if self.config.profile == "v4":
            self.env.v4_helpers = True
            self.env.v4_deadline = self.deadline
            self.env.lifecycle_sink = self.lifecycle_event
            container = getattr(self.env, "container", None)
            if container is not None:
                state.metadata.setdefault("v4", {})["container"] = {
                    "id": container.id,
                    "run_id": self.env.run_id,
                    "instance_id": self.env.instance_id,
                }
        self.files = RepoFiles(self.env)
        self.shell_tools = ShellTools(self.env, self.config)
        self.jobs = self.shell_tools.jobs
        self.hooks = (
            HookEngine(
                self.env,
                self.config,
                state,
                pre=agent.python_hooks["PreToolUse"],
                post=agent.python_hooks["PostToolUse"],
                submit=agent.python_hooks["PreSubmit"],
                ask=getattr(agent, "permission_ask", None),
            )
            if self.config.hooks_enabled
            else None
        )
        shared_index = getattr(agent, "shared_code_index", None)
        self.index, self.index_stats = shared_index or BM25Index.from_repository(self.env)
        self.file_tools = FileTools(self.files, self.state, self.config)
        self.validation = (
            ValidationRunner(agent, state) if self.config.structured_validation else None
        )
        self.evidence_policy = EvidencePolicy(self.env, state) if self.validation else None
        self.search_tools = SearchTools(self.index)
        self.submit_tool = SubmitTool(self)
        self.agent_tools = AgentTools(self)
        handlers = {
            "bash": self.bash,
            "view": self.view,
            "str_replace": self.replace,
            "search_code": self.search,
            "submit": self.submit,
        }
        self.registry = ToolRegistry(
            ToolSpec(
                item["function"]["name"],
                item,
                item["function"]["name"] in {"view", "search_code"},
                handlers[item["function"]["name"]],
            )
            for item in TOOLS
        )
        if self.validation:
            self.registry.add(
                ToolSpec(
                    "run_tests",
                    schema(
                        "run_tests",
                        "Run pytest directly (no shell). Returns structured, workspace-bound "
                        "local test evidence. Run after edits; local PASS is not official task resolution.",
                        {
                            "runner": {"type": "string", "enum": ["pytest"], "default": "pytest"},
                            "targets": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                            "args": {"type": "array", "items": {"type": "string"}},
                            "timeout": {
                                "type": "integer",
                                "maximum": self.config.run_tests_timeout,
                            },
                        },
                        ["targets"],
                    ),
                    False,
                    self.validation.run,
                )
            )
        self.registry.add(
            ToolSpec(
                "grep",
                schema(
                    "grep",
                    "Search repository text; return file and line matches.",
                    {
                        "pattern": {"type": "string"},
                        "path_glob": {"type": "string"},
                        "max_results": {"type": "integer", "default": 50},
                    },
                    ["pattern"],
                ),
                True,
                lambda args: ToolResult(grep(self.env, **args)),
            )
        )
        if self.config.apply_patch_enabled:
            self.registry.add(
                ToolSpec(
                    "apply_patch",
                    schema(
                        "apply_patch",
                        "Apply an atomic multi-file text patch in /testbed; "
                        "Python syntax failures roll back all files.",
                        {"patch": {"type": "string"}},
                        ["patch"],
                    ),
                    False,
                    self.apply_patch,
                )
            )
        if self.config.plan_tool:
            self.registry.add(
                ToolSpec(
                    "update_plan",
                    schema(
                        "update_plan",
                        "Record task steps; at most one in_progress. This is optional, not a submit gate.",
                        {
                            "steps": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "step": {"type": "string"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["pending", "in_progress", "completed"],
                                        },
                                    },
                                    "required": ["step", "status"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        ["steps"],
                    ),
                    False,
                    lambda args: ToolResult(update_plan(self.state, args["steps"])),
                )
            )
        if self.config.background_shell:
            self.registry.specs["bash"] = ToolSpec(
                "bash",
                schema(
                    "bash",
                    "Run a command in /testbed; timeout leaves a background job alive.",
                    {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "default": 120, "maximum": 600},
                        "run_in_background": {"type": "boolean", "default": False},
                    },
                    ["command"],
                ),
                False,
                self.bash,
            )
            self.registry.add(
                ToolSpec(
                    "job_output",
                    schema(
                        "job_output",
                        "Read background command output and status.",
                        {
                            "job_id": {"type": "string"},
                            "tail_lines": {"type": "integer", "default": 100},
                        },
                        ["job_id"],
                    ),
                    True,
                    self.job_output,
                )
            )
            self.registry.add(
                ToolSpec(
                    "job_kill",
                    schema(
                        "job_kill",
                        "Stop a background job process group.",
                        {"job_id": {"type": "string"}},
                        ["job_id"],
                    ),
                    False,
                    lambda args: self.shell_result(self.jobs.kill(args["job_id"])),
                )
            )
        if state.depth == 0 and self.config.subagents in {"explore", "both"}:
            self.registry.add(
                ToolSpec(
                    "explore",
                    schema(
                        "explore",
                        "Delegate read-only exploration; only a bounded summary returns.",
                        {
                            "question": {"type": "string"},
                            "thoroughness": {
                                "type": "string",
                                "enum": ["quick", "medium", "thorough"],
                            },
                        },
                        ["question"],
                    ),
                    True,
                    self.explore,
                )
            )
        if state.depth == 0 and self.config.subagents in {"verify", "both"}:
            self.registry.add(
                ToolSpec(
                    "verify",
                    schema(
                        "verify",
                        "Independently run relevant tests; workspace edits are restored.",
                        {"focus": {"type": "string"}},
                    ),
                    False,
                    self.verify,
                )
            )
        if hasattr(agent, "subagent_mode"):
            props = (
                {"summary": {"type": "string", "maxLength": 1500}}
                if agent.subagent_mode == "explore"
                else {
                    "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                    "evidence": {"type": "string"},
                }
            )
            self.registry.add(
                ToolSpec(
                    "report",
                    schema("report", "Return the final child report.", props, props),
                    False,
                    self.report,
                )
            )
        if hasattr(agent, "allowed_tools"):
            self.registry = ToolRegistry(
                s for name, s in self.registry.specs.items() if name in agent.allowed_tools
            )

    def execute(self, call):
        started = time.monotonic()
        args, error = parse_tool_arguments(call["arguments"])
        try:
            if self.deadline:
                self.deadline.check()
            if error:
                raise ValueError(error)
            hooked = {**call, "args": args}
            if not self.hooks and self.config.permissions_enabled:
                decision = command_policy(
                    hooked, self.state, self.config, getattr(self.agent, "permission_ask", None)
                )
                if isinstance(decision, Deny):
                    return ToolResult(decision.reason, metadata={"permission_denied": True})
            if self.hooks:
                decision = self.hooks.pre(hooked)
                if isinstance(decision, (Block, Deny)):
                    return ToolResult(decision.reason, metadata={"hook_blocked": True})
                before = self.hooks.before()
            if self.validation and call["name"] == "run_tests":
                self.validation.call_id = call["id"]
            result = self.registry.execute(call["name"], hooked["args"])
            if getattr(self.agent, "subagent_mode", None) == "verify" and call["name"] == "bash":
                self.state.metadata.setdefault("verification_commands", []).append(
                    {
                        "command": hooked["args"]["command"],
                        "exit_code": result.exit_code,
                        "verification": is_validation_command(
                            hooked["args"]["command"], self.config.verification_patterns
                        ),
                        "output": result.content,
                    }
                )
            if self.hooks:
                result = self.hooks.post(hooked, result, before)
        except TaskDeadline as exc:
            self.state.termination = "task_deadline"
            result = ToolResult(str(exc), metadata={"execution_state": "TIMEOUT", "error": True})
        except Exception as exc:
            result = ToolResult(f"{call['name']} error: {exc}", metadata={"error": True})
            if call["name"] == "str_replace":
                result.metadata["str_replace_failures"] = 1
        if self.deadline:
            if self.deadline.remaining() <= 0:
                self.state.termination = "task_deadline"
            result.metadata.setdefault(
                "execution_state", "TOOL_ERROR" if result.metadata.get("error") else "EXITED"
            )
        if self.evidence_policy:
            try:
                status = self.evidence_policy.current()
            except Exception as exc:
                status = {"state": "UNVERIFIED", "reason": "workspace unavailable: " + str(exc)}
            if not self.state.metadata.get("submit_forced"):
                self.state.metadata.setdefault("v4", {})["local_validation"] = status
            result.metadata["producer"] = "harness"
            result.metadata["validation_state"] = status["state"]
        path = None
        if self.config.progress_monitor and call["name"] == "run_tests":
            # Ignore UUIDs/artifact hashes/timings while retaining the actual failure output.
            try:
                validation_message = json.loads(result.content)
                result.metadata["progress_observation_signature"] = digest(
                    [
                        validation_message.get("local_validation"),
                        normalize_observation(validation_message.get("output", "")),
                    ]
                )
            except ValueError:
                pass
        if len(result.content) > self.config.tool_output_max_chars:
            # Call IDs are provider-controlled. Never use them as filesystem paths.
            key = hashlib.sha256(call["id"].encode()).hexdigest()[:16]
            path = f"/tmp/repofix_out_{self.state.step}_{key}.txt"
            self.env.write_text_file(path, result.content)
        observation = format_tool_observation(
            result.content,
            "",
            path,
            self.config.tool_output_max_chars,
            self.config.tool_output_head_chars,
            self.config.tool_output_tail_chars,
        )
        result.content = observation.content
        if result.metadata.get("shell"):
            if result.metadata.get("still_running"):
                header = f"still running: job_id={result.metadata['job_id']}"
            else:
                header = (
                    f"exit_code: {result.exit_code} | "
                    f"duration: {result.metadata.get('duration', 0):.3f}s | "
                    f"truncated: {'yes' if observation.truncated else 'no'}"
                )
                if benign_exit(result.metadata.get("command", ""), result.exit_code):
                    header += " | benign_exit"
                    result.metadata["benign_exit"] = True
            result.content = header + "\n" + result.content
            if self.deadline:
                result.content = (
                    "execution_state: " + result.metadata["execution_state"] + "\n" + result.content
                )
                if result.metadata.get("sigpipe_note"):
                    result.content += "\n" + result.metadata["sigpipe_note"]
        result.metadata.update(
            {key: value for key, value in asdict(observation).items() if key != "content"}
        )
        result.metadata["duration_seconds"] = time.monotonic() - started
        result.metadata["returned_chars"] = len(result.content)
        return result

    def lifecycle_event(self, event):
        self.state.events.append(event)
        self.state.metadata.setdefault("v4", {}).setdefault("cleanup_events", []).append(event)
        self.agent.trace.write(event)

    def cleanup(self):
        if self.deadline:
            self.deadline.enforcing = (
                False  # bounded cleanup and patch capture get their own timeout
            )
            for event in self.jobs.cleanup():
                self.lifecycle_event(event)

    def bash(self, args):
        return self.shell_tools.bash(args)

    def shell_result(self, args):
        return self.shell_tools.shell_result(args)

    def job_output(self, args):
        return self.shell_tools.job_output(args)

    def view(self, args):
        return self.file_tools.view(args)

    def replace(self, args):
        return self.file_tools.replace(args)

    def apply_patch(self, args):
        return self.file_tools.apply_patch(args)

    def search(self, args):
        return self.search_tools.search(args)

    def submit(self, args):
        return self.submit_tool.submit(args)

    def explore(self, args):
        return self.agent_tools.explore(args)

    def verify(self, args):
        return self.agent_tools.verify(args)

    def report(self, args):
        return self.agent_tools.report(args)
