"""Small environment adapter for repository fingerprints and file operations."""
import hashlib
import json
import shlex


def run_python(env, source):
    result = env.execute("python -c " + shlex.quote(source), timeout=60)
    if result.exit_code != 0 or result.timed_out:
        raise RuntimeError(result.output or "repository helper failed")
    return result.output


def fingerprint(env):
    """Hash dirty file contents too: porcelain status alone misses repeat edits."""
    if hasattr(env, "files"):
        return {path: hashlib.sha256(env.files.get(path, "[deleted]").encode()).hexdigest()
                for path in env.files.keys() | env.baseline.keys()
                if env.files.get(path) != env.baseline.get(path)}
    source = '''import subprocess, hashlib, json, pathlib
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
print(json.dumps(result))'''
    return json.loads(run_python(env, source))
