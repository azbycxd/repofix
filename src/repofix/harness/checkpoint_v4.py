"""V4 generations: pair durable state with a validated, content-addressed workspace."""

import base64
import hashlib
import io
import json
import os
import re
import tarfile
import time
import docker
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from .checkpoint import CheckpointStore, atomic_write, restore_workspace
from .compat import PY36_PATH_HELPER
from .deadline import TaskDeadline
from .state import Budget, RunState
from .validation import EvidencePolicy, workspace_signature
from .workspace import run_python


CAPTURE_SOURCE = (
    PY36_PATH_HELPER
    + """
import subprocess, tarfile, io, json, base64
limit = {limit}
patch = subprocess.check_output(['git', 'diff', '--binary', 'HEAD', '--'])
names = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z']).split(b'\\0')
archive = io.BytesIO(); total = len(patch)
if total > limit: raise ValueError('snapshot_refused: patch over limit')
with tarfile.open(fileobj=archive, mode='w') as tar:
    for raw in names:
        if not raw: continue
        name = raw.decode('utf-8'); path = Path(name)
        if any(p in ('__pycache__', '.pytest_cache') for p in path.parts): continue
        if path.is_symlink() or not path.is_file(): raise ValueError('snapshot requires regular untracked files')
        resolve(name)
        total += path.stat().st_size + 1024
        if total > limit: raise ValueError('snapshot_refused: untracked files over limit')
        info = tar.gettarinfo(str(path), arcname=name)
        info.mtime = 0; info.uid = 0; info.gid = 0; info.uname = ''; info.gname = ''
        with path.open('rb') as handle: tar.addfile(info, handle)
payload = archive.getvalue()
if len(patch) + len(payload) > limit: raise ValueError('snapshot_refused: tar bytes over limit')
print(json.dumps(dict(kind='docker', patch=base64.b64encode(patch).decode(),
    modified_paths=subprocess.check_output(['git', 'diff', '--name-only', '-z', 'HEAD']).decode().split('\\0'),
    base_head=subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip(),
    untracked_tar=base64.b64encode(payload).decode())))
"""
)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def forbidden_path(name):
    path = PurePosixPath(name)
    return (
        path.is_absolute()
        or ".." in path.parts
        or ".git" in path.parts
        or "\\" in name
        or "\0" in name
        or any(
            part == ".env"
            or part.startswith(".env.")
            or part in {"credentials", "id_rsa", "id_ed25519", ".aws"}
            for part in path.parts
        )
    )


def validate_snapshot(snapshot, limit, secrets=(), redact=lambda value: value):
    if snapshot.get("kind") == "fake":
        files = snapshot["files"]
        if any(forbidden_path(p) for p in files):
            raise ValueError("snapshot_refused: unsafe path or credential filename")
        parts = [encoded(snapshot)]
    elif snapshot.get("kind") == "docker":
        if any(forbidden_path(p) for p in snapshot.get("modified_paths", []) if p):
            raise ValueError("snapshot_refused: sensitive modified path")
        patch = base64.b64decode(snapshot["patch"], validate=True)
        archive = base64.b64decode(snapshot["untracked_tar"], validate=True)
        parts = [patch, archive]
        if sum(map(len, parts)) > limit:
            raise ValueError("snapshot_refused: uncompressed size limit")
        # Validate paths, regular types and exact bytes before any restore mutation.
        seen, total = set(), len(patch)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            for member in tar:
                if forbidden_path(member.name) or not member.isfile() or member.name in seen:
                    raise ValueError("snapshot_refused: unsafe tar member")
                seen.add(member.name)
                total += member.size
                if total > limit or len(tar.extractfile(member).read()) != member.size:
                    raise ValueError("snapshot_refused: invalid tar size")
        for line in patch.decode("utf-8", errors="replace").splitlines():
            if line.startswith(("+++ b/", "--- a/")) and forbidden_path(line[6:]):
                raise ValueError("snapshot_refused: sensitive patch path")
    else:
        raise ValueError("snapshot_refused: unknown format")
    total = sum(map(len, parts))
    if total > limit:
        raise ValueError("snapshot_refused: uncompressed size limit")
    for part in parts:
        text = part.decode("utf-8", errors="replace")
        if any(secret.encode() in part for secret in secrets if secret) or redact(text) != text:
            raise ValueError("snapshot_refused: credential detected")
    return total


def durable_write(path, data):
    atomic_write(path, data)
    descriptor = os.open(str(Path(path).parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class V4CheckpointStore(CheckpointStore):
    schema_version = 4

    def __init__(self, run_dir, redact=lambda x: x, secrets=(), max_bytes=64 * 1024 * 1024, keep=4):
        super().__init__(run_dir, redact, secrets)
        self.max_bytes, self.keep = max_bytes, keep
        self.load_events = []

    def capture(self, env):
        if hasattr(env, "files"):
            if env.symlinks:
                raise ValueError("snapshot_refused: fake symlink snapshots unsupported")
            return {"kind": "fake", "files": dict(env.files), "baseline": dict(env.baseline)}
        return json.loads(run_python(env, CAPTURE_SOURCE.replace("{limit}", str(self.max_bytes))))

    def save(self, state, env, reason="end_step"):
        started = time.monotonic()
        env.v4_helpers = True
        before = workspace_signature(env)
        snapshot = self.capture(env)
        size = validate_snapshot(snapshot, self.max_bytes, self.secrets, self.redact)
        if workspace_signature(env) != before:
            raise ValueError("snapshot_refused: workspace changed during capture")
        data = encoded(snapshot)
        digest = sha(data)
        self.directory.mkdir(parents=True, exist_ok=True)
        generations = list(self.directory.glob("generation-*.json"))
        generation = max((int(p.stem.split("-")[-1]) for p in generations), default=0) + 1
        name = "workspace-" + digest + ".json"
        blob = self.directory / name
        if not blob.exists() or sha(blob.read_bytes()) != digest:
            durable_write(blob, data)
        facts = state.metadata.setdefault("v4", {})
        facts["checkpoint_generation"] = generation
        state.metadata["v4"]["snapshot_workspace_signature"] = before
        manifest = {
            "schema_version": self.schema_version,
            "checkpoint_generation": generation,
            "snapshot_sha256": digest,
            "snapshot_bytes": size,
            "workspace": name,
            "workspace_signature": before,
            "reason": reason,
            "state": self.redact(asdict(state)),
        }
        manifest["manifest_sha256"] = sha(encoded(manifest))
        durable_write(self.directory / f"generation-{generation:08d}.json", encoded(manifest))
        gc_started = time.monotonic()
        self.collect()
        event = {
            "type": "checkpoint",
            "producer": "harness",
            "step": state.step,
            "checkpoint_generation": generation,
            "snapshot_bytes": size,
            "snapshot_sha256": digest,
            "reason": reason,
            "checkpoint_duration": time.monotonic() - started,
            "gc_duration": time.monotonic() - gc_started,
            "retained_generations": len(list(self.directory.glob("generation-*.json"))),
        }
        state.events.append(event)
        return event

    def _read(self, path):
        manifest = json.loads(path.read_bytes())
        expected = manifest.pop("manifest_sha256")
        if sha(encoded(manifest)) != expected or manifest["schema_version"] != self.schema_version:
            raise ValueError("manifest hash/schema mismatch")
        if path.stem != f"generation-{manifest['checkpoint_generation']:08d}":
            raise ValueError("generation mismatch")
        name = manifest["workspace"]
        if not re.fullmatch(r"workspace-[0-9a-f]{64}\.json", name):
            raise ValueError("unsafe workspace reference")
        blob = self.directory / name
        if blob.stat().st_size > self.max_bytes * 2 + 4096:
            raise ValueError("snapshot_refused: serialized size limit")
        payload = blob.read_bytes()
        if sha(payload) != manifest["snapshot_sha256"]:
            raise ValueError("snapshot hash mismatch")
        snapshot = json.loads(payload)
        validate_snapshot(snapshot, self.max_bytes, self.secrets, self.redact)
        raw = deepcopy(manifest["state"])
        raw["budget"] = Budget(**raw["budget"])
        state = RunState(**raw)
        if (
            type(state.step) is not int
            or state.step < 0
            or state.budget.estimated_cost < 0
            or state.budget.prompt_tokens < 0
            or not isinstance(state.pending_calls, list)
        ):
            raise ValueError("invalid checkpoint state")
        return state, snapshot, manifest

    def collect(self):
        valid = []
        paths = sorted(self.directory.glob("generation-*.json"), reverse=True)
        for path in paths:
            try:
                self._read(path)
                valid.append(path)
            except (ValueError, OSError, KeyError, TypeError, tarfile.TarError):
                continue
        keep = set(valid[: self.keep])
        for path in valid[self.keep :]:
            path.unlink()
        # All retained manifests, including malformed ones, prevent unsafe inferred deletion.
        referenced = set()
        for path in self.directory.glob("generation-*.json"):
            try:
                referenced.add(json.loads(path.read_bytes())["workspace"])
            except (ValueError, KeyError, TypeError):
                return
        for blob in self.directory.glob("workspace-*.json"):
            if blob.name not in referenced:
                blob.unlink()
        return len(keep)

    def load(self):
        self.load_events = []
        for path in sorted(self.directory.glob("generation-*.json"), reverse=True):
            try:
                state, snapshot, manifest = self._read(path)
                self.loaded_manifest = manifest
                return state, snapshot
            except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
                self.load_events.append(
                    {
                        "type": "checkpoint_error",
                        "producer": "harness",
                        "generation_file": path.name,
                        "error": str(exc),
                    }
                )
        raise ValueError("no complete valid V4 generation found; V3 files are not auto-migrated")

    def resume(self, env):
        state, snapshot = self.load()
        self.require_resumable(state)
        env.v4_helpers = True
        self.cleanup_previous_container(state, env)
        if snapshot["kind"] == "docker":
            actual = env.execute("git rev-parse HEAD").output.strip()
            if actual != snapshot["base_head"]:
                raise ValueError("refusing restore onto different base revision")
        restore_workspace(env, snapshot)
        restored_signature = workspace_signature(env)
        if restored_signature != self.loaded_manifest["workspace_signature"]:
            raise ValueError("restored workspace does not match generation; no model continuation")
        state.events.extend(self.load_events)
        seen = {m.get("tool_call_id") for m in state.messages if m.get("role") == "tool"}
        for call in state.pending_calls:
            if call["id"] not in seen:
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": "[INTERRUPTED_UNKNOWN: tool effects are uncertain; restored checkpoint workspace. "
                        "Inspect files and test evidence; this tool was NOT replayed.]",
                    }
                )
                seen.add(call["id"])
        state.pending_calls = []
        for job in state.metadata.get("v4", {}).get("background_jobs", []):
            if job.get("state") == "RUNNING":
                job["state"] = "LOST_AFTER_RESTART"
                state.events.append({"type": "job", "producer": "harness", **job})
        state.metadata.setdefault("v4", {})["local_validation"] = EvidencePolicy(
            env, state
        ).current()
        state.termination = None
        state.metadata.pop("usage_anchor", None)
        state.events.append(
            {
                "type": "resume",
                "producer": "harness",
                "checkpoint_generation": self.loaded_manifest["checkpoint_generation"],
                "workspace_signature": restored_signature,
            }
        )
        return state

    @staticmethod
    def cleanup_previous_container(state, env):
        previous = state.metadata.get("v4", {}).get("container")
        if not previous or not getattr(env, "client", None) or previous["id"] == env.container.id:
            return
        event = {"type": "cleanup", "producer": "harness", "old_container": previous["id"]}
        try:
            old = env.client.containers.get(previous["id"])
            labels = old.attrs.get("Config", {}).get("Labels", {})
            if (
                labels.get("repofix.run_id") != previous["run_id"]
                or labels.get("repofix.instance_id") != previous["instance_id"]
            ):
                raise ValueError("old container ownership label mismatch")
            old.remove(force=True)
            event["status"] = "REMOVED"
        except docker.errors.NotFound:
            event["status"] = "ALREADY_ABSENT"
        except Exception as exc:
            event.update(type="cleanup_error", status="ERROR", error=str(exc))
            state.events.append(event)
            raise RuntimeError("cannot safely clean previous run container") from exc
        state.events.append(event)


def checkpoint_store(config, run_dir, redact=lambda x: x, secrets=()):
    if config.checkpoint_limits:
        return V4CheckpointStore(
            run_dir,
            redact,
            secrets,
            config.checkpoint_max_bytes,
            config.checkpoint_keep_generations,
        )
    return CheckpointStore(run_dir, redact, secrets)


def save_boundary(store, state, runtime, reason):
    if store is None:
        return True
    if not isinstance(store, V4CheckpointStore):
        store.save(state, runtime.env)
        return True
    try:
        if runtime.deadline:
            runtime.deadline.check()
        jobs = []
        for job_id in runtime.jobs.jobs:
            status = runtime.jobs.output(job_id, tail_lines=1)
            jobs.append(
                {
                    "id": job_id,
                    "command": status["command"],
                    "state": "RUNNING" if status["still_running"] else "EXITED",
                }
            )
        if jobs:
            state.metadata.setdefault("v4", {})["background_jobs"] = jobs
        event = store.save(state, runtime.env, reason)
        runtime.agent.trace.write(event)
        return True
    except Exception as exc:
        expired = (
            isinstance(exc, TaskDeadline) or runtime.deadline and runtime.deadline.remaining() <= 0
        )
        event = {
            "type": "deadline" if expired else "checkpoint_error",
            "producer": "harness",
            "reason": reason,
            "error": str(exc),
            "step": state.step,
        }
        state.events.append(event)
        runtime.agent.trace.write(event)
        state.termination = "task_deadline" if expired else "checkpoint_error"
        for call in state.pending_calls:
            state.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": "[not executed: " + state.termination + "]",
                }
            )
        state.pending_calls = []
        return False
