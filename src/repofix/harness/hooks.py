"""Callable/command hook protocol; hook rejection is a normal observation."""

import json
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock

from repofix.reproduction import reproduction_key

from .permissions import DEFAULT_RULES, PermissionPolicy, split_commands, unwrap
from .workspace import fingerprint


def is_validation_command(command, patterns=()):
    if any(re.search(pattern, command) for pattern in patterns):
        return True
    try:
        commands = [unwrap(part) for part in split_commands(command)]
    except ValueError:
        return False
    for tokens in commands:
        if not tokens:
            continue
        if tokens[0] in {"pytest", "py.test", "tox"}:
            return True
        if tokens[0].startswith("python"):
            if tokens[1:3] in (["-m", "pytest"], ["-m", "unittest"]):
                return True
            if (
                len(tokens) > 1
                and tokens[1] != "-c"
                and reproduction_key(" ".join(tokens)) is not None
            ):
                return True
    return False


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Deny:
    reason: str


@dataclass(frozen=True)
class Rewrite:
    args: dict


@dataclass(frozen=True)
class Block:
    reason: str


class ExternalHook:
    """Trusted local command (argv, never shell=True); exit 2 blocks."""

    def __init__(self, command, timeout=30):
        self.command, self.timeout = command, timeout

    def __call__(self, *args):
        result_value = None
        if len(args) == 1:
            (state,) = args
            call, event = {"name": "submit"}, "PreSubmit"
        elif len(args) == 3:
            call, result_value, state = args
            event = "PostToolUse"
        else:
            call, state = args
            event = "PreToolUse"
        try:
            result = subprocess.run(
                self.command,
                input=json.dumps(
                    {
                        "event": event,
                        "call": call,
                        "state": asdict(state),
                        "result": asdict(result_value) if result_value else None,
                    }
                ),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Deny(f"hook execution failed: {exc}")
        if result.returncode == 2:
            if result_value is not None:
                result_value.content += "\n[PostToolUse blocked] " + result.stderr.strip()
                result_value.metadata["hook_blocked"] = True
                return result_value
            return Deny(result.stderr.strip() or "blocked by external hook")
        if result.returncode:
            return Deny(f"hook failed ({result.returncode}): {result.stderr.strip()}")
        if result.stdout.strip():
            try:
                data = json.loads(result.stdout)
            except ValueError:
                return Deny("external hook returned invalid JSON")
            if isinstance(data, dict) and isinstance(data.get("rewrite"), dict):
                return Rewrite(data["rewrite"])
            if (
                result_value is not None
                and isinstance(data, dict)
                and isinstance(data.get("content"), str)
            ):
                result_value.content = data["content"]
        return result_value if result_value is not None else Allow()


def command_policy(call, state, config, ask=None):
    if call["name"] != "bash":
        return Allow()
    if config.permissions_enabled:
        policy = (
            PermissionPolicy.load(config.permissions_file)
            if config.permissions_file
            else PermissionPolicy(DEFAULT_RULES, config.permission_default)
        )
        decision = policy.decide(call["args"].get("command", ""))
        state.events.append(
            {"type": "permission", "decision": decision.decision, "reason": decision.reason}
        )
        if decision.decision == "deny":
            return Deny(decision.reason)
        if decision.decision == "ask" and (
            config.evaluation_mode or ask is None or not ask(decision.reason)
        ):
            return Deny("permission requires approval: " + decision.reason)
    return Allow()


def verify_before_submit(state):
    if state.last_validation_version == state.workspace_version and state.last_validation:
        return Allow()
    if state.submit_blocks >= 3:
        state.metadata["submit_forced"] = True
        return Allow()
    state.submit_blocks += 1
    return Block(
        "Run a successful verification command after the last file change before submitting."
    )


def syntax_check(env, changed, result):
    for path in sorted(changed):
        if not path.endswith(".py") or changed[path] is None:
            continue
        check = env.execute("python -m py_compile " + shlex.quote(path), timeout=60)
        if check.exit_code != 0 or check.timed_out:
            result.content += f"\n[syntax_check: {path}]\n{check.output}"
            result.metadata["syntax_check_failed"] = True
    return result


class HookEngine:
    def __init__(self, env, config, state, pre=(), post=(), submit=(), ask=None):
        self.env, self.config, self.state = env, config, state
        self.pre_hooks, self.post_hooks, self.submit_hooks = list(pre), list(post), list(submit)
        self.lock = RLock()
        self.ask = ask
        if config.hooks_file:
            data = json.loads(Path(config.hooks_file).read_text())
            for event, target in (
                ("PreToolUse", self.pre_hooks),
                ("PostToolUse", self.post_hooks),
                ("PreSubmit", self.submit_hooks),
            ):
                for item in data.get(event, []):
                    command = item["command"]
                    if (
                        not isinstance(command, list)
                        or not command
                        or any(not isinstance(v, str) for v in command)
                    ):
                        raise ValueError("hook command must be a nonempty argv list")
                    target.append(ExternalHook(command, item.get("timeout", 30)))
        if config.hooks_enabled:
            self.pre_hooks.insert(0, self._command_policy)
            self.post_hooks.insert(0, self._syntax_check)
            if not config.structured_validation:
                self.submit_hooks.append(verify_before_submit)
        self.changed = {}

    def _command_policy(self, call, state):
        return command_policy(call, state, self.config, self.ask)

    def _syntax_check(self, call, result, state):
        return syntax_check(self.env, self.changed, result)

    def pre(self, call):
        rewritten = False
        for hook in self.pre_hooks:
            decision = hook(call, self.state)
            if isinstance(decision, (Deny, Block)):
                self.state.count("hook_blocks")
                self.state.events.append(
                    {
                        "type": "hook",
                        "event": "PreToolUse",
                        "tool": call["name"],
                        "decision": "deny",
                        "reason": decision.reason,
                    }
                )
                return decision
            if isinstance(decision, Rewrite):
                call["args"] = decision.args
                rewritten = True
                self.state.events.append(
                    {
                        "type": "hook",
                        "event": "PreToolUse",
                        "tool": call["name"],
                        "decision": "rewrite",
                    }
                )
        self.state.events.append(
            {"type": "hook", "event": "PreToolUse", "tool": call["name"], "decision": "allow"}
        )
        # A rewrite must not bypass the policy that checked the original args.
        final_decision = (
            command_policy(call, self.state, self.config, self.ask) if rewritten else Allow()
        )
        if isinstance(final_decision, Deny):
            self.state.count("hook_blocks")
            return final_decision
        return Allow()

    def before(self):
        with self.lock:
            current = fingerprint(self.env)
            previous = self.state.metadata.get("workspace_fingerprint")
            if previous is not None and previous != current:
                self.state.workspace_version += 1
            self.state.metadata["workspace_fingerprint"] = current
            return current

    def post(self, call, result, before):
        with self.lock:
            after = fingerprint(self.env)
            changed = {
                p: after.get(p)
                for p in before.keys() | after.keys()
                if before.get(p) != after.get(p)
            }
            if changed:
                self.state.workspace_version += 1
            self.state.metadata["workspace_fingerprint"] = after
            self.changed = changed
            command = result.metadata.get("command", "")
            valid = is_validation_command(command, self.config.verification_patterns)
            if valid:
                self.state.last_validation = {
                    "command": command,
                    "output": result.content,
                    "exit_code": result.exit_code,
                    "step": self.state.step,
                }
                if (
                    not self.config.structured_validation
                    and result.exit_code == 0
                    and not result.metadata.get("timed_out")
                    and not result.metadata.get("still_running")
                ):
                    self.state.last_validation_version = self.state.workspace_version
                else:
                    self.state.last_validation_version = -1
            for hook in self.post_hooks:
                replacement = hook(call, result, self.state)
                if isinstance(replacement, (Deny, Block)):
                    result.content += "\n[PostToolUse error] " + replacement.reason
                    result.metadata["hook_blocked"] = True
                elif hasattr(replacement, "content") and hasattr(replacement, "metadata"):
                    result = replacement
                else:
                    result.content += "\n[PostToolUse error] invalid hook result"
                    result.metadata["hook_blocked"] = True
            self.state.events.append(
                {
                    "type": "hook",
                    "event": "PostToolUse",
                    "tool": call["name"],
                    "decision": "observed",
                }
            )
            return result

    def pre_submit(self):
        if self.state.submit_blocks >= 3:
            self.state.metadata["submit_forced"] = True
            return Allow()
        for hook in self.submit_hooks:
            blocks_before = self.state.submit_blocks
            decision = hook(self.state)
            if not isinstance(decision, (Allow, Block, Deny)):
                decision = Block("invalid PreSubmit hook result")
            if isinstance(decision, (Block, Deny)):
                if self.state.submit_blocks == blocks_before:
                    self.state.submit_blocks += 1
                self.state.count("hook_blocks")
                self.state.events.append(
                    {
                        "type": "hook",
                        "event": "PreSubmit",
                        "decision": "block",
                        "reason": decision.reason,
                    }
                )
                return decision
        self.state.events.append({"type": "hook", "event": "PreSubmit", "decision": "allow"})
        return Allow()
