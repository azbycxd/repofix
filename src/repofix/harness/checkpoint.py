"""Atomic state manifests and content-addressed workspace snapshots."""
import base64
import hashlib
import io
import json
import os
import tarfile
import uuid
from dataclasses import asdict
from pathlib import Path

from .state import RunState, Budget
from .workspace import run_python, PATH_HELPER


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def capture_workspace(env):
    if hasattr(env, "files"):
        return {"kind": "fake", "files": dict(env.files), "baseline": dict(env.baseline)}
    source = '''import subprocess, pathlib, tarfile, io, json, base64
patch = subprocess.check_output(['git', 'diff', '--binary', 'HEAD', '--'])
names = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z']).split(b'\\0')
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode='w') as tar:
    for name in names:
        if not name: continue
        name = name.decode('utf-8'); p = pathlib.Path(name)
        if p.is_symlink() or not p.is_file(): raise ValueError('snapshot requires regular untracked files')
        if not p.resolve().is_relative_to(pathlib.Path('/testbed').resolve()): raise ValueError('snapshot path escape')
        if p.name == '.env' or p.name.startswith('.env.'): raise ValueError('refusing to snapshot untracked environment credentials')
        tar.add(p, arcname=name, recursive=False)
print(json.dumps({'kind': 'docker', 'patch': base64.b64encode(patch).decode(),
                  'untracked_tar': base64.b64encode(archive.getvalue()).decode()}))
'''
    return json.loads(run_python(env, source))


def restore_workspace(env, snapshot):
    if snapshot["kind"] == "fake":
        env.files, env.baseline = dict(snapshot["files"]), dict(snapshot["baseline"])
        return
    # Restoration is ONLY inside the disposable container. Validate tar paths
    # before resetting or applying anything; no host tar extraction.
    payload = base64.b64decode(snapshot["untracked_tar"])
    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
        for m in tar.getmembers():
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts or ".git" in p.parts or not m.isfile():
                raise ValueError("unsafe snapshot member")
    snapshot_path = f"/tmp/repofix_restore_{uuid.uuid4().hex}.json"
    env.write_text_file(snapshot_path, json.dumps(snapshot))
    source = PATH_HELPER + f"\nimport json\nsnapshot_file = Path({snapshot_path!r})\nsnapshot = json.loads(snapshot_file.read_text())\nsnapshot_file.unlink()\n" + '''import base64, subprocess, io, tarfile
archive = tarfile.open(fileobj=io.BytesIO(base64.b64decode(snapshot['untracked_tar'])))
for member in archive.getmembers(): resolve(member.name)
# Disposable /testbed only; never execute a host git reset.
subprocess.run(['git', 'reset', '--hard', 'HEAD'], check=True, stdout=subprocess.DEVNULL)
names = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z']).split(b'\\0')
for name in names:
    if name:
        p = resolve(name.decode()); p.unlink(missing_ok=True)
patch = base64.b64decode(snapshot['patch'])
if patch.strip(): subprocess.run(['git', 'apply', '--binary', '--index', '-'], input=patch, check=True)
for member in archive.getmembers():
    p = resolve(member.name); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(archive.extractfile(member).read()); p.chmod(member.mode)
'''
    run_python(env, source)


class CheckpointStore:
    def __init__(self, run_dir, redact=lambda x: x, secrets=()):
        self.directory = Path(run_dir) / "checkpoints"
        self.redact, self.secrets = redact, [s for s in secrets if s]

    def save(self, state, env):
        snapshot = capture_workspace(env)
        data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
        raw_parts = [data]
        if snapshot["kind"] == "docker":
            raw_parts += [base64.b64decode(snapshot["patch"]), base64.b64decode(snapshot["untracked_tar"])]
        if any(secret.encode() in part for secret in self.secrets for part in raw_parts):
            raise ValueError("credential detected in workspace snapshot; checkpoint refused")
        digest = hashlib.sha256(data).hexdigest()
        atomic_write(self.directory / f"workspace-{digest}.json", data)
        manifest = {"version": 1, "state": self.redact(asdict(state)), "workspace": f"workspace-{digest}.json", "sha256": digest}
        atomic_write(self.directory / f"step-{state.step:04d}.json", json.dumps(manifest, ensure_ascii=False).encode())

    def load(self):
        for path in sorted(self.directory.glob("step-*.json"), reverse=True):
            try:
                manifest = json.loads(path.read_text())
                if manifest.get("version") != 1:
                    continue
                name = manifest["workspace"]
                if Path(name).name != name or not name.startswith("workspace-"):
                    continue
                data = (self.directory / name).read_bytes()
                if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
                    continue
                raw = manifest["state"]
                raw["budget"] = Budget(**raw["budget"])
                state = RunState(**raw)
                if type(state.step) is not int or state.step < 0 or not isinstance(state.messages, list) or not isinstance(state.pending_calls, list):
                    continue
                if state.budget.estimated_cost < 0 or state.budget.prompt_tokens < 0:
                    continue
                return state, json.loads(data)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        raise ValueError("no complete valid checkpoint found")

    def resume(self, env):
        state, snapshot = self.load()
        restore_workspace(env, snapshot)
        for call in state.pending_calls:
            state.messages.append({"role": "tool", "tool_call_id": call["id"],
                "content": "[interrupted: tool call did not complete before the run stopped]"})
        state.pending_calls = []
        if state.termination in {"interrupted", "provider_error"}:
            state.termination = None
        state.metadata.pop("usage_anchor", None)
        return state
