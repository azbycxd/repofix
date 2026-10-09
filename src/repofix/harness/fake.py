"""Deterministic in-memory repository and scriptable shell for offline tests."""

import difflib
import posixpath
import re
import shlex
from copy import deepcopy

from repofix.env import DockerEnv, ExecutionResult, StrReplaceResult


class FakeEnv:
    workdir = "/testbed"

    def __init__(self, files=None, commands=None):
        self.files = dict(files or {"example.py": "VALUE = 1\n"})
        self.baseline = deepcopy(self.files)
        self.commands = dict(commands or {})
        self.executed = []
        self.output_files = {}
        self.symlinks = {}

    def resolve(self, path):
        if not isinstance(path, str) or not path or "\x00" in path or ".." in path.split("/"):
            raise ValueError("invalid path or traversal")
        path = posixpath.normpath(path)
        if path.startswith("/"):
            if not path.startswith("/testbed/"):
                raise ValueError("path outside /testbed")
            path = path[len("/testbed/") :]
        for link, target in self.symlinks.items():
            if path == link or path.startswith(link + "/"):
                raise ValueError("symlink paths are not permitted")
        return path

    def execute(self, command, timeout=60):
        self.executed.append(command)
        value = self.commands.get(command)
        if callable(value):
            value = value(self)
        if isinstance(value, BaseException):
            raise value
        if value is not None:
            return value if isinstance(value, ExecutionResult) else ExecutionResult(*value)
        if command.startswith("git apply "):
            patch = self.output_files.get(shlex.split(command)[-1], "")
            if patch.startswith("diff --git "):
                self.apply_unified_patch(patch)
        if command.startswith("python -m py_compile "):
            try:
                for path in shlex.split(command)[3:]:
                    compile(self.files[self.resolve(path)], path, "exec")
            except (SyntaxError, KeyError) as exc:
                return ExecutionResult(str(exc), 1, False, 0)
        return ExecutionResult("", 0, False, 0)

    def apply_unified_patch(self, patch):
        """Minimal ordinary text diff application for the offline judge fixture."""
        for section in re.split(r"(?m)(?=^diff --git )", patch):
            if not section.strip():
                continue
            lines = section.splitlines(True)
            plus = next(line[6:].strip() for line in lines if line.startswith("+++ b/"))
            path = self.resolve(plus)
            original = self.files.get(path, "").splitlines(True)
            output, cursor, index = [], 0, 0
            while index < len(lines):
                line = lines[index]
                if not line.startswith("@@"):
                    index += 1
                    continue
                match = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", line)
                if not match:
                    raise ValueError("invalid fixture hunk")
                start = max(0, int(match[1]) - 1)
                output.extend(original[cursor:start])
                cursor = start
                index += 1
                while index < len(lines) and not lines[index].startswith("@@"):
                    value = lines[index]
                    index += 1
                    if value.startswith((" ", "-")):
                        if cursor >= len(original) or original[cursor] != value[1:]:
                            raise ValueError("fixture context mismatch")
                        cursor += 1
                    if value.startswith((" ", "+")):
                        output.append(value[1:])
            output.extend(original[cursor:])
            self.files[path] = "".join(output)

    def read_repository_text_files(self, max_file_bytes=1_000_000):
        return dict(self.files)

    def view_file(self, path, start_line, end_line):
        path = self.resolve(path)
        return f"/testbed/{path}\n" + DockerEnv._numbered_lines(
            self.files[path], start_line, end_line
        )

    def str_replace_file(self, path, old_str, new_str):
        path = self.resolve(path)
        original = self.files[path]
        if not old_str or original.count(old_str) != 1:
            return StrReplaceResult(
                "str_replace error: expected exactly 1 occurrence", False, False
            )
        updated = original.replace(old_str, new_str, 1)
        if path.endswith(".py"):
            try:
                compile(updated, path, "exec")
            except SyntaxError as exc:
                return StrReplaceResult(
                    f"syntax check failed; original restored: {exc}", False, True
                )
        self.files[path] = updated
        return StrReplaceResult("Replacement successful.\n" + updated, True, False)

    def write_text_file(self, path, content):
        self.output_files[path] = content

    def get_tracked_changes(self):
        return [
            ("M" if path in self.files else "D", path)
            for path in self.baseline
            if self.files.get(path) != self.baseline[path]
        ]

    def get_diff(self):
        chunks = []
        for path in sorted(self.baseline.keys() | self.files.keys()):
            old, new = self.baseline.get(path, ""), self.files.get(path, "")
            if old != new:
                chunks.append(f"diff --git a/{path} b/{path}\n")
                chunks.extend(
                    difflib.unified_diff(
                        old.splitlines(True),
                        new.splitlines(True),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                    )
                )
        return "".join(chunks)
