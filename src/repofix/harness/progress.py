"""Deterministic, bounded V4 progress feedback; never makes a correctness verdict."""

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass

from .hooks import is_validation_command
from .workspace import fingerprint


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def normalize_observation(content):
    content = re.sub(r"\b\d{4}-\d\d-\d\d[T ][\d:.+-]+Z?", "<time>", content)
    content = re.sub(
        r"\b(?:duration|elapsed|latency)(?:_seconds)?:\s*[\d.]+s?", "duration:<time>", content
    )
    content = re.sub(r"\bin \d+(?:\.\d+)?s\b", "in <time>", content)
    content = re.sub(r"\b[0-9a-f]{16,64}\b", "<opaque-id>", content)
    return content.strip()


@dataclass
class ProgressSample:
    step: int
    workspace_signature: str
    tool_signatures: list
    observation_signatures: list
    new_files_read_or_new_file_versions: list
    new_failure_signatures_or_new_test_outcome: bool
    pending_background_job_state: list
    plan_change: bool
    signal: str
    repeat_count: int
    no_progress_steps: int
    action: str | None = None
    feedback: str | None = None
    duration_seconds: float = 0.0
    feedback_bytes: int = 0


class ProgressMonitor:
    def __init__(self, config, env, state):
        self.config, self.env, self.state = config, env, state
        self.data = state.metadata.setdefault("v4", {}).setdefault("progress", {})
        # Reuse HookEngine's content-sensitive dirty fingerprint. No BM25 rebuild.
        self.data.setdefault("workspace", digest(fingerprint(env)))
        for name, value in (
            ("reads", {}),
            ("seen_results", []),
            ("no_progress", 0),
            ("replans", 0),
            ("polls", {}),
            ("duration_seconds", 0.0),
            ("feedback_bytes", 0),
            ("samples", 0),
        ):
            self.data.setdefault(name, value)

    def observe(self, calls, observations):
        started = time.monotonic()
        data, state = self.data, self.state
        workspace = state.metadata.get("workspace_fingerprint")
        signature = digest(workspace if workspace is not None else fingerprint(self.env))
        changed = signature != data["workspace"]
        reads = [p for p, version in state.file_reads.items() if data["reads"].get(p) != version]
        tool_sigs, obs_sigs, waiting, new_results = [], [], [], False
        error = False
        for call, observation in zip(calls, observations):
            name = call["name"]
            try:
                args = json.loads(call["arguments"])
            except (ValueError, TypeError):
                args = call["arguments"]
            normalized = normalize_observation(observation["content"])
            tool_sigs.append(digest([name, args]))
            obs_sig = observation.get("progress_observation_signature") or digest(normalized)
            obs_sigs.append(obs_sig)
            error |= bool(observation.get("error") or observation.get("hook_blocked"))
            if observation.get("still_running") and observation.get("job_id"):
                job = observation["job_id"]
                data["polls"][job] = data["polls"].get(job, 0) + 1
                if data["polls"][job] <= self.config.progress_job_poll_budget:
                    waiting.append(job)
            informative = name in {"view", "grep", "search_code", "run_tests"}
            informative |= name in {"bash", "job_output"} and is_validation_command(
                observation.get("command", "")
            )
            # Pure plan/natural language or echo commands cannot count as evidence.
            if informative and not error and obs_sig not in data["seen_results"]:
                data["seen_results"].append(obs_sig)
                new_results = True
        validation = state.metadata.get("v4", {}).get("validation_evidence", {})
        test_signature = digest(
            [
                validation.get(k)
                for k in (
                    "outcome",
                    "tests_collected",
                    "tests_passed",
                    "tests_failed",
                    "tests_errors",
                    "tests_skipped",
                )
            ]
        )
        test_changed = bool(validation) and test_signature != data.get("test_signature")
        repeat = digest([signature, tool_sigs, obs_sigs])
        data["repeat_count"] = (
            data.get("repeat_count", 0) + 1 if repeat == data.get("repeat") else 1
        )
        progress = changed or bool(reads) or new_results or test_changed
        signal = (
            "WORKSPACE_CHANGED"
            if changed
            else "VALIDATION_CHANGED"
            if test_changed
            else "NEW_EVIDENCE"
            if reads or new_results
            else "WAITING_FOR_JOB"
            if waiting
            else "TOOL_ERROR"
            if error
            else "SAME_RESULT"
            if calls
            else "NONE"
        )
        if progress:
            data["no_progress"] = 0
        elif not waiting:
            data["no_progress"] += 1
        action, feedback = None, None
        if not waiting and not state.termination:
            if data["no_progress"] >= self.config.progress_no_progress_steps:
                if data["replans"] < self.config.progress_max_replans:
                    data["replans"] += 1
                    data["no_progress"] = 0
                    action = "REPLAN"
                    feedback = (
                        "No new file version, workspace change or test evidence. "
                        "Change the concrete localization/verification action; restating a plan is not progress."
                    )
                else:
                    action = "STUCK"
                    state.termination = "stuck"
                    state.submitted = False
                    feedback = (
                        "Bounded replans exhausted without new evidence; "
                        "retaining candidate patch as unsubmitted."
                    )
            elif data["repeat_count"] == self.config.progress_repeat_warn:
                action = "WARN"
                feedback = (
                    "Same tool arguments, workspace and normalized observation repeated. "
                    "Inspect a new file/range or change the test hypothesis."
                )
        if feedback and action != "STUCK":
            state.messages.append(
                {"role": "user", "content": "[Harness progress " + action + "] " + feedback}
            )
        sample = ProgressSample(
            state.step,
            signature,
            tool_sigs,
            obs_sigs,
            reads,
            test_changed or new_results,
            waiting,
            any(c["name"] == "update_plan" for c in calls),
            signal,
            data["repeat_count"],
            data["no_progress"],
            action,
            feedback,
            time.monotonic() - started,
            len((feedback or "").encode()),
        )
        data.update(
            workspace=signature,
            reads=dict(state.file_reads),
            repeat=repeat,
            test_signature=test_signature,
        )
        data["duration_seconds"] += sample.duration_seconds
        data["feedback_bytes"] += sample.feedback_bytes
        data["samples"] += 1
        return {"type": "progress", "producer": "harness", **asdict(sample)}
