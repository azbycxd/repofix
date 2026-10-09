"""Command-prefix policy is guidance, NOT the sandbox security boundary."""
import json
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Decision:
    decision: str
    reason: str


DEFAULT_RULES = [
    {"decision": "deny", "prefix": "git push"},
    {"decision": "deny", "prefix": "rm -rf /"},
    {"decision": "deny", "prefix": "curl"},
    {"decision": "deny", "prefix": "wget"},
    {"decision": "ask", "prefix": "pip install"},
    {"decision": "ask", "prefix": "python -m pip install"},
]


def split_commands(command):
    parts, buffer = [], []
    quote = None
    escape = False
    for char in command:
        if escape:
            buffer.append(char); escape = False
        elif char == "\\" and quote != "'":
            buffer.append(char); escape = True
        elif quote:
            buffer.append(char)
            if char == quote: quote = None
        elif char in "'\"":
            quote = char; buffer.append(char)
        elif char in ";|&\n":
            if "".join(buffer).strip(): parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    if quote or escape:
        raise ValueError("unclosed shell quoting")
    if "".join(buffer).strip(): parts.append("".join(buffer))
    return [shlex.split(part) for part in parts]


def unwrap(tokens):
    tokens = list(tokens)
    while tokens:
        if "=" in tokens[0] and not tokens[0].startswith("-"):
            tokens.pop(0)
        elif Path(tokens[0]).name in {"nohup", "command"}:
            tokens.pop(0)
            if tokens and tokens[0] == "--": tokens.pop(0)
        elif Path(tokens[0]).name == "env":
            tokens.pop(0)
            while tokens and (tokens[0].startswith("-") or "=" in tokens[0]):
                option = tokens.pop(0)
                if option in {"-u", "--unset", "-C", "--chdir"} and tokens: tokens.pop(0)
        elif Path(tokens[0]).name == "timeout":
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                option = tokens.pop(0)
                if option in {"-s", "--signal", "-k", "--kill-after"} and tokens: tokens.pop(0)
            if tokens: tokens.pop(0)  # duration
        else:
            break
    if tokens:
        tokens[0] = Path(tokens[0]).name
    return tokens


class PermissionPolicy:
    def __init__(self, rules=(), default="allow"):
        if default not in {"allow", "ask", "deny"}:
            raise ValueError("invalid default permission")
        self.default = default
        self.rules = []
        for rule in rules:
            if rule.get("decision") not in {"allow", "ask", "deny"} or not isinstance(rule.get("prefix"), str) or not rule["prefix"].strip():
                raise ValueError("rule requires valid decision and nonempty prefix")
            self.rules.append((rule["decision"], shlex.split(rule["prefix"])))

    @classmethod
    def load(cls, path):
        path = Path(path)
        data = tomllib.loads(path.read_text()) if path.suffix == ".toml" else json.loads(path.read_text())
        return cls(data.get("rules", []), data.get("default", "allow"))

    def decide(self, command):
        try:
            segments = split_commands(command)
        except (ValueError, TypeError) as exc:
            return Decision("deny", str(exc))
        decisions = []
        for segment in segments:
            tokens = unwrap(segment)
            matches = [(decision, prefix) for decision, prefix in self.rules if tokens[:len(prefix)] == prefix]
            selected = next(((d, p) for d, p in matches if d == "deny"), None)
            selected = selected or next(((d, p) for d, p in matches if d == "ask"), None)
            selected = selected or (matches[0] if matches else (self.default, tokens))
            decisions.append(Decision(selected[0], " ".join(selected[1])))
        return next((d for d in decisions if d.decision == "deny"), None) or next((d for d in decisions if d.decision == "ask"), None) or Decision("allow", "allowed by policy")
