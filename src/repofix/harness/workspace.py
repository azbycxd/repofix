"""Small environment adapter for repository fingerprints and file operations."""

import hashlib
import json
import posixpath
import shlex
import uuid

from .compat import compatible_source


def run_python(env, source):
    prefix = ""
    if getattr(env, "v4_helpers", False):
        source = compatible_source(source, PATH_HELPER)
        prefix = "LC_ALL=C.UTF-8 LANG=C.UTF-8 PYTHONIOENCODING=utf-8 "
    result = env.execute(prefix + "python -c " + shlex.quote(source), timeout=60)
    if result.exit_code != 0 or result.timed_out:
        raise RuntimeError(result.output or "repository helper failed")
    return result.output


def fingerprint(env):
    """Hash dirty file contents too: porcelain status alone misses repeat edits."""
    if hasattr(env, "files"):
        return {
            path: hashlib.sha256(env.files.get(path, "[deleted]").encode()).hexdigest()
            for path in env.files.keys() | env.baseline.keys()
            if env.files.get(path) != env.baseline.get(path)
        }
    source = """import subprocess, hashlib, json, pathlib
raw = subprocess.check_output(['git', 'status', '--porcelain=v1', '-z', '--untracked-files=all'])
parts = raw.split(b'\\0'); result = {}; i = 0
while i < len(parts):
    item = parts[i]; i += 1
    if not item: continue
    status = item[:2].decode(); path = item[3:].decode('utf-8')
    if 'R' in status or 'C' in status: i += 1
    p = pathlib.Path(path)
    if p.is_symlink(): data = str(p.readlink()).encode()
    elif p.is_file(): data = p.read_bytes()
    else: data = b'[deleted]'
    result[path] = hashlib.sha256(data).hexdigest()
print(json.dumps(result))"""
    return json.loads(run_python(env, source))


PATH_HELPER = """from pathlib import Path
root = Path('/testbed').resolve()
def resolve(value):
    if not isinstance(value, str) or not value or '\\0' in value or '..' in Path(value).parts:
        raise ValueError('invalid path or traversal')
    p = Path(value)
    p = p if p.is_absolute() else root / p
    p = p.resolve()
    if not p.is_relative_to(root) or p == root or '.git' in p.relative_to(root).parts:
        raise ValueError('path must stay in /testbed outside .git')
    return p
"""


class RepoFiles:
    def __init__(self, env):
        self.env = env

    def resolve(self, path):
        if not isinstance(path, str) or not path or "\x00" in path or ".." in path.split("/"):
            raise ValueError("invalid path or traversal")
        candidate = posixpath.normpath(path)
        if candidate.startswith("/"):
            if not candidate.startswith("/testbed/"):
                raise ValueError("path outside /testbed")
            candidate = candidate[len("/testbed/") :]
        if candidate == "." or ".git" in candidate.split("/"):
            raise ValueError("repository metadata paths are not permitted")
        if hasattr(self.env, "files"):
            return self.env.resolve(candidate)
        return json.loads(
            run_python(
                self.env,
                PATH_HELPER
                + f"\nimport json\nprint(json.dumps(str(resolve({candidate!r}).relative_to(root))))",
            )
        )

    def exists(self, path):
        path = self.resolve(path)
        if hasattr(self.env, "files"):
            return path in self.env.files
        return json.loads(
            run_python(
                self.env,
                PATH_HELPER + f"\nimport json\nprint(json.dumps(resolve({path!r}).exists()))",
            )
        )

    def read(self, path):
        path = self.resolve(path)
        if hasattr(self.env, "files"):
            return self.env.files[path]
        return json.loads(
            run_python(
                self.env,
                PATH_HELPER
                + f"\nimport json\nprint(json.dumps(resolve({path!r}).read_text(encoding='utf-8')))",
            )
        )

    def apply(self, updates):
        updates = {self.resolve(p): value for p, value in updates.items()}
        if hasattr(self.env, "files"):
            original = dict(self.env.files)
            try:
                for path, content in updates.items():
                    if content is None:
                        self.env.files.pop(path, None)
                    else:
                        self.env.files[path] = content
                for path, content in updates.items():
                    if path.endswith(".py") and content is not None:
                        compile(content, path, "exec")
            except Exception:
                self.env.files = original
                raise
            return
        # Validate ALL paths/read originals before any write; syntax failure
        # restores every file, including adds/deletes/moves. No host paths used.
        payload_path = f"/tmp/repofix_apply_{uuid.uuid4().hex}.json"
        source = (
            PATH_HELPER
            + f"\npayload_path = Path({payload_path!r})\n"
            + """import subprocess, os, json
updates = json.loads(payload_path.read_text(encoding='utf-8'))
targets = {name: resolve(name) for name in updates}
saved = {name: (p.read_bytes(), p.stat().st_mode) if p.exists() else None for name,p in targets.items()}
index_path = Path(subprocess.check_output(['git', 'rev-parse', '--git-path', 'index'], text=True).strip())
index_data = index_path.read_bytes() if index_path.exists() else None
try:
    for name, content in updates.items():
        p = targets[name]
        if content is None: p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8')
    for name, content in updates.items():
        if name.endswith('.py') and content is not None:
            subprocess.run(['python', '-m', 'py_compile', str(targets[name])], check=True)
    added = [name for name, old in saved.items() if old is None and updates[name] is not None]
    if added: subprocess.run(['git', 'add', '--', *added], check=True)
except BaseException:
    for name, old in saved.items():
        p = targets[name]
        if old is None: p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(old[0]); p.chmod(old[1])
    if index_data is None: index_path.unlink(missing_ok=True)
    else: index_path.write_bytes(index_data)
    raise
"""
        )
        with self.env.temporary_patch_file(payload_path, json.dumps(updates)):
            run_python(self.env, source)
