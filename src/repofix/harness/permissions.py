"""Command-prefix policy is guidance, NOT the sandbox security boundary."""
import json
import re
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


def extract_substitutions(command):
    """Keep shell quoting while extracting executable substitutions recursively."""
    outer, nested = [], []
    quote = None
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\" and quote != "'":
            outer.append(command[index:index + 2])
            index += 2
            continue
        if quote != "'" and (command.startswith("$(", index) or char == "`"):
            backtick = char == "`"
            start = index + (1 if backtick else 2)
            end, depth, inner_quote = start, 1, None
            while end < len(command):
                value = command[end]
                if value == "\\" and inner_quote != "'":
                    end += 2
                    continue
                if backtick and value == "`":
                    break
                if inner_quote:
                    if value == inner_quote:
                        inner_quote = None
                elif value in "'\"":
                    inner_quote = value
                elif not backtick:
                    if value == "(":
                        depth += 1
                    elif value == ")":
                        depth -= 1
                        if depth == 0:
                            break
                end += 1
            if end >= len(command):
                raise ValueError("unclosed command substitution")
            nested.extend(split_commands(command[start:end]))
            outer.append("__substitution__")
            index = end + 1
            continue
        outer.append(char)
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        index += 1
    return "".join(outer), nested


REDIRECTION = re.compile(r"(?:\d+)?(?:&>>|&>|[<>]&|>>|<<-?|<>|>\||[<>])")


def skip_redirect_target(command, index):
    while index < len(command) and command[index].isspace():
        index += 1
    start, quote = index, None
    while index < len(command):
        char = command[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char.isspace() or char in ";|&<>":
            break
        index += 1
    if quote or index == start:
        raise ValueError("invalid redirection target")
    return index


def split_commands(command):
    command, nested = extract_substitutions(command)
    parts, buffer = [], []
    quote = None
    escape = False
    index = 0
    while index < len(command):
        char = command[index]
        if not quote and not escape:
            redirect = REDIRECTION.match(command, index)
            if redirect and (not char.isdigit() or not buffer or buffer[-1].isspace()):
                index = skip_redirect_target(command, redirect.end())
                buffer.append(" ")
                continue
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
        index += 1
    if quote or escape:
        raise ValueError("unclosed shell quoting")
    if "".join(buffer).strip(): parts.append("".join(buffer))
    return nested + [shlex.split(part) for part in parts if shlex.split(part)]


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
