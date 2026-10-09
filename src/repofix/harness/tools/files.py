import fnmatch
import re
import shlex


def grep(env, pattern, path_glob=None, max_results=50):
    if (
        not isinstance(pattern, str)
        or not pattern
        or not isinstance(max_results, int)
        or isinstance(max_results, bool)
        or not 1 <= max_results <= 1000
    ):
        raise ValueError("grep requires a pattern and max_results in 1..1000")
    if path_glob is not None and (
        not isinstance(path_glob, str) or path_glob.startswith("/") or ".." in path_glob.split("/")
    ):
        raise ValueError("path_glob must stay inside /testbed")
    if hasattr(env, "files"):
        matches = []
        for path, text in sorted(env.files.items()):
            if path_glob and not fnmatch.fnmatch(path, path_glob):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if re.search(pattern, line):
                    matches.append(f"{path}:{number}:{line}")
        return "\n".join(matches[:max_results]) or "No matches."
    glob = f" --glob {shlex.quote(path_glob)}" if path_glob else ""
    include = f" --include={shlex.quote(path_glob)}" if path_glob else ""
    command = (
        f"if command -v rg >/dev/null 2>&1; then rg -n --no-heading --color never{glob} -- {shlex.quote(pattern)} .; "
        f"else grep -rnE{include} -- {shlex.quote(pattern)} .; fi"
    )
    result = env.execute(command)
    if result.exit_code not in (0, 1):
        raise ValueError(result.output or "grep failed")
    return "\n".join(result.output.splitlines()[:max_results]) or "No matches."


import hashlib

from repofix.env import DockerEnv

from .patch import prepare_patch
from .result import ToolResult


class FileTools:
    def __init__(self, files, state, config):
        self.files, self.state, self.config = files, state, config
        self.env = files.env

    def view(self, args):
        path = self.files.resolve(args["path"])
        text = self.files.read(path)
        start, end = args["start_line"], args["end_line"]
        if type(start) is not int or type(end) is not int:
            raise ValueError("line bounds must be integers")
        content = f"/testbed/{path}\n" + DockerEnv._numbered_lines(text, start, end)
        self.state.file_reads[path] = hashlib.sha256(text.encode()).hexdigest()
        return ToolResult(content)

    def read_guard(self, path, content):
        if (
            self.config.read_before_edit
            and self.state.file_reads.get(path) != hashlib.sha256(content.encode()).hexdigest()
        ):
            raise ValueError(
                f"view {path} before editing: never read or content changed since view"
            )

    def replace(self, args):
        path = self.files.resolve(args["path"])
        original = self.files.read(path)
        self.read_guard(path, original)
        old, new = args["old_str"], args["new_str"]
        if (
            not isinstance(old, str)
            or not isinstance(new, str)
            or not old
            or original.count(old) != 1
        ):
            raise ValueError("old_str must be nonempty and occur exactly once; strings required")
        updated = original.replace(old, new, 1)
        try:
            self.files.apply({path: updated})
        except (SyntaxError, RuntimeError) as exc:
            return ToolResult(
                f"str_replace syntax check failed; original restored: {exc}",
                metadata={"str_replace_failures": 1, "syntax_rollbacks": 1},
            )
        lines = updated.splitlines()
        self.refresh_reads({path: updated})
        line = updated[: updated.index(new)].count("\n") if new and new in updated else 0
        context = "\n".join(
            f"{i + 1}: {lines[i]}"
            for i in range(max(0, line - 2), min(len(lines), line + new.count("\n") + 3))
        )
        return ToolResult(f"Replacement successful: {path}\n{context}")

    def refresh_reads(self, updates):
        for path, content in updates.items():
            if content is None:
                self.state.file_reads.pop(path, None)
            else:
                self.state.file_reads[path] = hashlib.sha256(content.encode()).hexdigest()

    def apply_patch(self, args):
        updates = prepare_patch(args["patch"], self.files, self.read_guard)
        try:
            self.files.apply(updates)
        except (SyntaxError, RuntimeError) as exc:
            return ToolResult(
                f"apply_patch failed; all files restored: {exc}",
                metadata={"syntax_rollbacks": 1, "error": True},
            )
        self.refresh_reads(updates)
        return ToolResult("Patch applied:\n" + "\n".join(updates))
