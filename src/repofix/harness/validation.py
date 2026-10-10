"""V4 local test evidence. Only direct Harness execution may issue evidence.

Local PASS attests these test cases on these bytes, not requirement coverage or
external Judge acceptance. Trusted repositories/tests are assumed; a root process
in the same container is not a tamper-proof attestation boundary.
"""

import hashlib
import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from repofix.env import ArgvExecutionResult

from .tools.result import ToolResult


def content_hash(value):
    return hashlib.sha256(value).hexdigest()


def json_hash(value):
    return content_hash(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


# Python 3.6-compatible; hashes actual tracked + nonignored untracked contents,
# file modes and symlink destinations. Git internals, ignored files, untracked
# __pycache__/.pytest_cache and /tmp are excluded; tracked cache paths still count.
# HEAD tree binds the baseline; metadata mtime is not evidence.
SIGNATURE_SOURCE = """import os, stat, hashlib, subprocess, json
tracked = set(subprocess.check_output(['git','ls-files','-c','-z']).split(b'\\0'))
untracked = subprocess.check_output(['git','ls-files','-o','--exclude-standard','-z']).split(b'\\0')
names = tracked.union(untracked)
items = []
for raw in sorted(set(names)):
    if not raw: continue
    name = raw.decode('utf-8'); p = os.path.join('/testbed',name)
    if raw not in tracked and any(part in ('__pycache__','.pytest_cache') for part in name.split('/')):
        continue
    try:
        mode = os.lstat(p).st_mode
        if stat.S_ISLNK(mode): value = os.readlink(p).encode()
        elif stat.S_ISREG(mode):
            h = hashlib.sha256()
            with open(p,'rb') as f:
                for part in iter(lambda: f.read(1024*1024), b''): h.update(part)
            items.append([name, stat.S_IMODE(mode), h.hexdigest()]); continue
        else: value = b'[nonregular]'
    except FileNotFoundError:
        mode = 0; value = b'[deleted]'
    items.append([name, stat.S_IMODE(mode), hashlib.sha256(value).hexdigest()])
tree = subprocess.check_output(['git','rev-parse','HEAD^{tree}']).decode().strip()
print(hashlib.sha256(json.dumps([tree,items],sort_keys=True).encode()).hexdigest())
"""


def workspace_signature(env):
    if hasattr(env, "files"):
        return json_hash({"baseline": env.baseline, "files": env.files, "symlinks": env.symlinks})
    result = env.execute_argv(["python", "-c", SIGNATURE_SOURCE], timeout=60)
    value = result.stdout.strip()
    if (
        result.execution_state != "EXITED"
        or result.exit_code
        or not re.fullmatch(r"[0-9a-f]{64}", value)
    ):
        raise RuntimeError("workspace signature unavailable: " + result.output[:1000])
    return value


@dataclass(frozen=True)
class ValidationEvidence:
    id: str
    run_id: str
    tool_call_id: str
    source: str
    runner: str
    argv: list[str]
    cwd: str
    test_targets: list[str]
    started_at: str
    finished_at: str
    duration_seconds: float
    workspace_signature_before: str
    workspace_signature_after: str | None
    completed: bool
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    execution_state: str
    tests_collected: int | None
    tests_passed: int | None
    tests_failed: int | None
    tests_errors: int | None
    tests_skipped: int | None
    outcome: str
    output_artifact: dict
    report_artifact: str | None
    artifact_hashes: dict
    parse_error: str | None
    evidence_hash: str = ""


def parse_junit(payload):
    if len(payload) > 8 * 1024 * 1024 or b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise ValueError("unsafe or oversized JUnit XML")
    root = ET.fromstring(payload)
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("expected JUnit testsuite(s)")
    cases = root.findall(".//testcase")
    counts = {
        "tests_collected": len(cases),
        "tests_failed": 0,
        "tests_errors": 0,
        "tests_skipped": 0,
    }
    for case in cases:
        statuses = [name for name in ("failure", "error", "skipped") if case.find(name) is not None]
        if len(statuses) > 1:
            raise ValueError("testcase has conflicting outcomes")
        if statuses:
            counts[
                {"failure": "tests_failed", "error": "tests_errors", "skipped": "tests_skipped"}[
                    statuses[0]
                ]
            ] += 1
    counts["tests_passed"] = len(cases) - sum(
        counts[k] for k in ("tests_failed", "tests_errors", "tests_skipped")
    )
    suites = root.findall(".//testsuite") if root.tag == "testsuites" else [root]
    declared = sum(int(s.get("tests", "0")) for s in suites if not s.findall("testsuite"))
    if declared != len(cases):
        raise ValueError("JUnit count does not match testcase records")
    return counts


def classify(result, counts, stable, parse_error):
    if (
        result.cancelled
        or result.timed_out
        or not result.completed
        or result.exit_code is None
        or result.error
    ):
        return "INCONCLUSIVE"
    if parse_error and parse_error.startswith("ENV_ERROR"):
        return "INCONCLUSIVE"
    if result.exit_code in (2, 3, 4, 5, 126, 127):
        return "INCONCLUSIVE"  # collection/config/environment/no-tests, not verified execution
    if result.exit_code != 0:
        return "FAIL"
    if counts and (counts["tests_failed"] or counts["tests_errors"]):
        return "FAIL"
    if parse_error or not stable or not counts or counts["tests_passed"] <= 0:
        return "INCONCLUSIVE"
    return "PASS"


def validate_request(args, config):
    if set(args) - {"runner", "targets", "args", "timeout"}:
        raise ValueError("unknown run_tests fields; report path and evidence are Harness-owned")
    if args.get("runner", "pytest") != "pytest":
        raise ValueError("UNSUPPORTED_RUNNER: only pytest has structured evidence")
    targets = args.get("targets")
    options = args.get("args", [])
    timeout = args.get("timeout", config.run_tests_timeout)
    if type(timeout) is not int or not 1 <= timeout <= config.run_tests_timeout:
        raise ValueError("invalid run_tests timeout")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
        raise ValueError("targets must contain 1..20 repository paths/node IDs")
    for target in targets:
        if not isinstance(target, str) or not 0 < len(target) <= 512:
            raise ValueError("invalid test target")
        path = PurePosixPath(target.split("::", 1)[0])
        if (
            path.is_absolute()
            or ".." in path.parts
            or ".git" in path.parts
            or target.startswith("-")
        ):
            raise ValueError("test target must stay inside /testbed")
        if any(char in target for char in "\0\n\r|;&><`$\\"):
            raise ValueError("shell operators are not test targets")
    if not isinstance(options, list) or len(options) > 16:
        raise ValueError("args must be a bounded list of permitted pytest options")
    allowed = {"-q", "-v", "-vv", "-x", "--disable-warnings", "--tb=short", "--tb=line", "--tb=no"}
    if any(
        not isinstance(v, str)
        or len(v) > 64
        or (v not in allowed and not re.fullmatch(r"--maxfail=(?:[1-9]|1[0-9]|20)", v))
        for v in options
    ):
        raise ValueError(
            "unsupported pytest option (collect-only/config/report/plugin overrides forbidden)"
        )
    return targets, options, timeout


class ValidationRunner:
    def __init__(self, agent, state):
        self.agent, self.state, self.env = agent, state, agent.env
        self.directory = agent.trace.path.parent / "validation"
        self.call_id = ""

    def run(self, args):
        targets, options, timeout = validate_request(args, self.agent.config)
        # Native path validation prevents symlink/node-ID escapes too.
        for target in targets:
            if hasattr(self.env, "files"):
                self.env.resolve(target.split("::", 1)[0])
            else:
                check = self.env.execute_argv(
                    [
                        "python",
                        "-c",
                        "import os,sys; p=os.path.realpath(sys.argv[1]); "
                        "assert p == '/testbed' or p.startswith('/testbed/'), 'test path escape'",
                        target.split("::", 1)[0],
                    ],
                    timeout=10,
                )
                if check.exit_code or check.execution_state != "EXITED":
                    raise ValueError("test target does not resolve inside /testbed")
        before = workspace_signature(self.env)
        identifier = uuid.uuid4().hex
        report_path = f"/tmp/repofix_validation/{identifier}.xml"
        argv = ["python", "-m", "pytest", *targets, *options, "--junitxml=" + report_path]
        self.directory.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        try:
            setup = self.env.execute_argv(["mkdir", "-p", "/tmp/repofix_validation"], timeout=10)
            if setup.exit_code != 0 or setup.execution_state != "EXITED":
                raise RuntimeError("cannot prepare validation report directory")
            result = self.env.execute_argv(argv, timeout=timeout)
        except Exception as exc:
            result = ArgvExecutionResult(
                "",
                str(exc),
                None,
                False,
                time.monotonic() - started,
                completed=False,
                error="ENV_ERROR: execution could not complete",
            )
        error, counts, report_artifact = result.error, None, None
        artifacts, hashes = {}, {}
        for suffix, value in (("stdout.txt", result.stdout), ("stderr.txt", result.stderr)):
            path = self.directory / f"{identifier}.{suffix}"
            content = self.agent.trace._redact_text(value).encode()
            path.write_bytes(content)
            artifacts[suffix] = "validation/" + path.name
            hashes[path.name] = content_hash(content)
        try:
            payload = self.env.read_validation_report(report_path)
            path = self.directory / (identifier + ".xml")
            safe = self.agent.trace._redact_text(payload.decode("utf-8", errors="replace")).encode()
            path.write_bytes(safe)
            report_artifact = "validation/" + path.name
            hashes[path.name] = content_hash(safe)
            counts = parse_junit(payload)
        except Exception as exc:
            error = "REPORT_UNAVAILABLE_OR_INVALID: " + str(exc)
        if "No module named pytest" in result.output or result.error:
            error = (
                "ENV_ERROR: pytest/runtime unavailable; inspect testbed interpreter and artifacts"
            )
        try:
            after = workspace_signature(self.env)
        except Exception as exc:
            after = None
            error = "WORKSPACE_UNAVAILABLE: " + str(exc)
        if before != after:
            error = "WORKSPACE_CHANGED_DURING_VALIDATION: " + (error or "source bytes changed")
        empty_counts = dict.fromkeys(
            ("tests_collected", "tests_passed", "tests_failed", "tests_errors", "tests_skipped")
        )
        evidence = ValidationEvidence(
            identifier,
            self.agent.trace.path.parent.name,
            self.call_id,
            "harness_run_tests",
            "pytest",
            argv,
            "/testbed",
            targets,
            started_at,
            datetime.now(timezone.utc).isoformat(),
            time.monotonic() - started,
            before,
            after,
            result.completed,
            result.exit_code,
            result.timed_out,
            result.cancelled,
            result.execution_state,
            **(counts or empty_counts),
            outcome=classify(result, counts, before == after, error),
            output_artifact=artifacts,
            report_artifact=report_artifact,
            artifact_hashes=hashes,
            parse_error=error,
        )
        record = asdict(evidence)
        safe = self.agent.trace._redact(record)
        safe["evidence_hash"] = json_hash({k: v for k, v in safe.items() if k != "evidence_hash"})
        (self.directory / (identifier + ".json")).write_text(json.dumps(safe, indent=2) + "\n")
        self.state.metadata.setdefault("v4", {})["validation_evidence"] = safe
        self.agent.trace.write(
            {"type": "validation", "producer": "harness", "step": self.state.step, "evidence": safe}
        )
        message = {
            "local_validation": "LOCAL_TESTS_PASSED"
            if record["outcome"] == "PASS"
            else record["outcome"],
            "scope": "specified local tests only; never TASK_RESOLVED",
            "output": self.agent.trace._redact_text(result.output),
            "evidence": safe,
        }
        return ToolResult(
            json.dumps(message, ensure_ascii=False),
            result.exit_code,
            {"validation_evidence_id": identifier, "execution_state": result.execution_state},
        )


class EvidencePolicy:
    def __init__(self, env, state):
        self.env, self.state = env, state

    def current(self):
        evidence = self.state.metadata.get("v4", {}).get("validation_evidence")
        if not evidence:
            return {"state": "UNVERIFIED", "evidence_id": None}
        expected = json_hash({k: v for k, v in evidence.items() if k != "evidence_hash"})
        if evidence.get("source") != "harness_run_tests" or expected != evidence.get(
            "evidence_hash"
        ):
            return {
                "state": "UNVERIFIED",
                "evidence_id": None,
                "reason": "invalid evidence integrity",
            }
        signature = workspace_signature(self.env)
        outcome = evidence["outcome"]
        if (
            signature != evidence["workspace_signature_after"]
            or signature != evidence["workspace_signature_before"]
        ):
            outcome = "STALE"
        return {
            "state": "LOCAL_TESTS_PASSED" if outcome == "PASS" else outcome,
            "evidence_id": evidence["id"],
            "evidence_hash": evidence["evidence_hash"],
            "workspace_signature": signature,
            "runner": evidence["runner"],
            "targets": evidence["test_targets"],
            "tests_collected": evidence["tests_collected"],
        }

    def before_submit(self):
        status = self.current()
        self.state.metadata.setdefault("v4", {})["local_validation"] = status
        if status["state"] == "LOCAL_TESTS_PASSED":
            return None
        if self.state.submit_blocks >= 3:
            self.state.metadata["submit_forced"] = True
            self.state.metadata["v4"]["local_validation"] = {
                **status,
                "observed_state": status["state"],
                "state": "UNVERIFIED",
            }
            return None
        self.state.submit_blocks += 1
        self.state.count("hook_blocks")
        return (
            "No current structured local PASS (" + status["state"] + "). "
            "Use run_tests with supported pytest targets after editing; bash output or "
            "a Verify verdict is not trusted evidence. Candidates may be saved unverified after 3 blocks."
        )
