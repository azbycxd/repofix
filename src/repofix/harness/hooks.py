"""Callable/command hook protocol; hook rejection is a normal observation."""
import json
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from threading import RLock

from repofix.reproduction import reproduction_key
from .workspace import fingerprint


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

    def __call__(self, call, state):
        try:
            result = subprocess.run(self.command, input=json.dumps({"call": call, "state": asdict(state)}),
                capture_output=True, text=True, timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Deny(f"hook execution failed: {exc}")
        if result.returncode == 2:
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
        return Allow()


def command_policy(call, state, config, ask=None):
    if call["name"] != "bash":
        return Allow()
    if config.permissions_enabled:
        from .permissions import PermissionPolicy, DEFAULT_RULES
        policy = PermissionPolicy.load(config.permissions_file) if config.permissions_file else PermissionPolicy(DEFAULT_RULES, config.permission_default)
        decision = policy.decide(call["args"].get("command", ""))
        state.events.append({"type": "permission", "decision": decision.decision, "reason": decision.reason})
        if decision.decision == "deny":
            return Deny(decision.reason)
        if decision.decision == "ask" and (config.evaluation_mode or ask is None or not ask(decision.reason)):
            return Deny("permission requires approval: " + decision.reason)
    return Allow()


def verify_before_submit(state):
    if state.last_validation_version == state.workspace_version and state.last_validation:
        return Allow()
    if state.submit_blocks >= 3:
        state.metadata["submit_forced"] = True
        return Allow()
    state.submit_blocks += 1
    return Block("Run a successful verification command after the last file change before submitting.")


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

    def pre(self, call):
        for hook in [lambda c, s: command_policy(c, s, self.config, self.ask), *self.pre_hooks]:
            decision = hook(call, self.state)
            if isinstance(decision, (Deny, Block)):
                self.state.count("hook_blocks")
                self.state.events.append({"type": "hook", "event": "PreToolUse", "tool": call["name"], "decision": "deny", "reason": decision.reason})
                return decision
            if isinstance(decision, Rewrite):
                call["args"] = decision.args
        return Allow()

    def before(self):
        return fingerprint(self.env)

    def post(self, call, result, before):
        with self.lock:
            after = fingerprint(self.env)
            changed = {p: after.get(p) for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
            if changed:
                self.state.workspace_version += 1
            result = syntax_check(self.env, changed, result)
            command = result.metadata.get("command", "")
            valid = reproduction_key(command) is not None or any(re.search(p, command) for p in self.config.verification_patterns)
            if valid:
                self.state.last_validation = {"command": command, "output": result.content,
                    "exit_code": result.exit_code, "step": self.state.step}
                if result.exit_code == 0 and not result.metadata.get("timed_out") and not result.metadata.get("still_running"):
                    self.state.last_validation_version = self.state.workspace_version
                else:
                    self.state.last_validation_version = -1
            for hook in self.post_hooks:
                result = hook(call, result, self.state)
            return result

    def pre_submit(self):
        for hook in [*self.submit_hooks, verify_before_submit]:
            decision = hook(self.state)
            if isinstance(decision, (Block, Deny)):
                self.state.count("hook_blocks")
                self.state.events.append({"type": "hook", "event": "PreSubmit", "decision": "block", "reason": decision.reason})
                return decision
        return Allow()
