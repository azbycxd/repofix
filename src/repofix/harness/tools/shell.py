"""Container-local background processes; host only polls JSON status."""

import json
import shlex
import time
import uuid

from ..permissions import split_commands, unwrap
from ..workspace import run_python

SHELL_ENV = {
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "NO_COLOR": "1",
    "TERM": "dumb",
    "PYTHONUNBUFFERED": "1",
}


def benign_exit(command, code):
    if code != 1:
        return False
    try:
        segments = split_commands(command)
        tokens = unwrap(segments[-1]) if segments else []
    except (ValueError, TypeError):
        return False
    return bool(tokens) and (
        tokens[0] in {"grep", "rg", "egrep", "fgrep", "diff", "test", "[", "find"}
        or tokens[:2] in [["git", "diff"], ["git", "grep"]]
    )


class JobManager:
    def __init__(self, env, reliable=False, max_jobs=2):
        self.env = env
        self.jobs = {}
        self.reliable, self.max_jobs = reliable, max_jobs

    def start(self, command):
        if (
            self.reliable
            and sum(self.output(j)["still_running"] for j in self.jobs) >= self.max_jobs
        ):
            raise ValueError("background job limit reached; inspect or stop an existing job")
        job_id = uuid.uuid4().hex[:16]
        job = {"id": job_id, "command": command, "start": time.monotonic(), "pid": None}
        if hasattr(self.env, "files"):
            job["result"] = self.env.execute(command)
        else:
            effective_command = "set -o pipefail\n" + command if self.reliable else command
            worker = """import subprocess, pathlib, sys
command, stem = sys.argv[1:]
with open(stem + '.log', 'wb', buffering=0) as out:
    p = subprocess.run(['bash', '-lc', command], cwd='/testbed', stdout=out, stderr=subprocess.STDOUT)
pathlib.Path(stem + '.exit').write_text(str(p.returncode))
"""
            source = f"""import subprocess, pathlib, os, json
folder = pathlib.Path('/tmp/repofix_jobs'); folder.mkdir(mode=0o700, exist_ok=True)
stem = str(folder / {job_id!r})
environment = dict(os.environ); environment.update({SHELL_ENV!r})
with open(os.devnull, 'wb') as null:
    p = subprocess.Popen(['python', '-c', {worker!r}, {effective_command!r}, stem], cwd='/testbed',
        env=environment, stdin=subprocess.DEVNULL, stdout=null, stderr=null, start_new_session=True)
print(json.dumps(p.pid))
"""
            job["pid"] = json.loads(run_python(self.env, source))
        self.jobs[job_id] = job
        return job_id

    def output(self, job_id, tail_lines=None):
        if job_id not in self.jobs:
            raise ValueError("unknown job_id")
        if tail_lines is not None and (type(tail_lines) is not int or not 1 <= tail_lines <= 10000):
            raise ValueError("tail_lines must be an integer in 1..10000")
        job = self.jobs[job_id]
        elapsed = time.monotonic() - job["start"]
        if hasattr(self.env, "files"):
            result = job["result"]
            code = job.get(
                "killed",
                result.exit_code
                if not result.timed_out and elapsed >= result.latency_seconds
                else None,
            )
            output = result.output
        else:
            source = f"""import pathlib, json
stem = pathlib.Path('/tmp/repofix_jobs') / {job_id!r}
log = pathlib.Path(str(stem) + '.log'); status = pathlib.Path(str(stem) + '.exit')
tail_lines = {tail_lines!r}
output = ''
if log.exists():
    if tail_lines is None:
        output = log.read_text(errors='replace')
    else:
        blocks = []
        with log.open('rb') as handle:
            handle.seek(0, 2); position = handle.tell(); newlines = 0
            while position > 0 and newlines <= tail_lines:
                size = min(8192, position); position -= size; handle.seek(position)
                block = handle.read(size); blocks.append(block); newlines += block.count(b'\\n')
        output = b''.join(reversed(blocks)).decode(errors='replace')
        output = '\\n'.join(output.splitlines()[-tail_lines:])
print(json.dumps({{'output': output,
                  'code': int(status.read_text()) if status.exists() else None}}))
"""
            data = json.loads(run_python(self.env, source))
            output, code = data["output"], data["code"]
        if tail_lines is not None:
            output = "\n".join(output.splitlines()[-tail_lines:])
        data = {
            "output": output,
            "exit_code": code,
            "duration": elapsed,
            "job_id": job_id,
            "still_running": code is None,
            "command": job["command"],
            "log_path": f"/tmp/repofix_jobs/{job_id}.log",
        }
        if self.reliable:
            data["execution_state"] = (
                "CANCELLED" if job.get("cancelled") else "RUNNING" if code is None else "EXITED"
            )
            if (
                hasattr(self.env, "files")
                and code is None
                and not job["result"].timed_out
                and elapsed >= job["result"].latency_seconds
            ):
                data.update(still_running=False, execution_state="ENV_ERROR")
            if code is None and not hasattr(self.env, "files"):
                status_source = f"""import pathlib, json
p = pathlib.Path('/proc/{job["pid"]}/stat')
try: alive = p.read_text().rsplit(')', 1)[1].split()[0] != 'Z'
except FileNotFoundError: alive = False
print(json.dumps(alive))
"""
                if not json.loads(run_python(self.env, status_source)):
                    data.update(still_running=False, execution_state="ENV_ERROR")
            data["pipefail_enabled"] = True
            data["pipeline_failure"] = bool(code not in (None, 0) and "|" in job["command"])
            if code == 141 and "|" in job["command"]:
                data["sigpipe_note"] = (
                    "SIGPIPE may mean an early-closing consumer (e.g. head), not a test verdict."
                )
        return data

    def wait(self, job_id, timeout):
        deadline = time.monotonic() + timeout
        delays, attempt = (0.1, 0.2, 0.5, 1.0), 0
        while True:
            data = self.output(job_id, tail_lines=100)
            if not data["still_running"]:
                return self.output(job_id)  # complete foreground output, once only
            if time.monotonic() >= deadline:
                return data
            time.sleep(
                min(delays[min(attempt, len(delays) - 1)], max(0, deadline - time.monotonic()))
            )
            attempt += 1

    def kill(self, job_id):
        if job_id not in self.jobs:
            raise ValueError("unknown job_id")
        job = self.jobs[job_id]
        if hasattr(self.env, "files"):
            job["killed"] = 143
        else:
            source = f"""import os, signal, pathlib
try: os.killpg({job["pid"]!r}, signal.SIGKILL)
except ProcessLookupError: pass
pathlib.Path('/tmp/repofix_jobs/{job_id}.exit').write_text('137')
"""
            run_python(self.env, source)
            if self.reliable:
                verify = f"""import pathlib, time, json
def live_group():
    for path in pathlib.Path('/proc').glob('[0-9]*/stat'):
        try: fields = path.read_text().rsplit(')', 1)[1].split()
        except (FileNotFoundError, PermissionError): continue
        if fields[0] != 'Z' and int(fields[2]) == {job["pid"]!r}: return True
    return False
for attempt in range(20):
    if not live_group(): break
    time.sleep(0.05)
if live_group(): raise RuntimeError('tracked process group still alive after SIGKILL')
"""
                run_python(self.env, verify)
        if self.reliable:
            job["cancelled"] = True
        return self.output(job_id)

    def cleanup(self):
        events = []
        for job_id in self.jobs:
            try:
                status = self.output(job_id, tail_lines=1)
                if status["still_running"] or status.get("execution_state") == "ENV_ERROR":
                    status = self.kill(job_id)
                events.append(
                    {
                        "type": "job_cleanup",
                        "producer": "harness",
                        "job_id": job_id,
                        "status": status.get("execution_state", "EXITED"),
                    }
                )
            except Exception as exc:
                events.append(
                    {
                        "type": "cleanup_error",
                        "producer": "harness",
                        "job_id": job_id,
                        "status": "ERROR",
                        "error": str(exc),
                    }
                )
        return events


def foreground(env, command, timeout):
    prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in SHELL_ENV.items())
    started = time.monotonic()
    result = env.execute(
        command if hasattr(env, "files") else prefix + " bash -lc " + shlex.quote(command), timeout
    )
    return {
        "output": result.output,
        "exit_code": result.exit_code,
        "duration": time.monotonic() - started,
        "command": command,
        "timed_out": result.timed_out,
        "still_running": False,
    }


from .result import ToolResult


class ShellTools:
    def __init__(self, env, config):
        self.env, self.config = env, config
        self.reliable = config.profile == "v4"
        self.jobs = JobManager(env, self.reliable, config.max_background_jobs)

    def bash(self, args):
        command, timeout = args["command"], args.get("timeout", 120)
        if (
            not isinstance(command, str)
            or not command.strip()
            or type(timeout) is not int
            or not 1 <= timeout <= 600
        ):
            raise ValueError("command required; timeout must be an integer in 1..600")
        if self.config.background_shell:
            job_id = self.jobs.start(command)
            data = (
                self.jobs.output(job_id)
                if args.get("run_in_background", False)
                else self.jobs.wait(
                    job_id,
                    min(timeout, self.env.v4_deadline.remaining())
                    if getattr(self.env, "v4_deadline", None)
                    else timeout,
                )
            )
        else:
            effective = (
                "set -o pipefail\n" + command
                if self.reliable and not hasattr(self.env, "files")
                else command
            )
            data = foreground(self.env, effective, timeout)
            if self.reliable:
                data["command"] = command
                data["execution_state"] = (
                    "TIMEOUT"
                    if data["timed_out"]
                    else "ENV_ERROR"
                    if data["exit_code"] is None
                    else "EXITED"
                )
                data["pipefail_enabled"] = True
                data["pipeline_failure"] = bool(
                    data["exit_code"] not in (None, 0) and "|" in command
                )
        if self.reliable and data.get("exit_code") == 141 and "|" in command:
            data["sigpipe_note"] = (
                "SIGPIPE may mean an early-closing consumer (e.g. head), not a test verdict."
            )
        return self.shell_result(data)

    @staticmethod
    def shell_result(data):
        return ToolResult(
            data["output"],
            data["exit_code"],
            {"shell": True, **{k: v for k, v in data.items() if k != "output"}},
        )

    def job_output(self, args):
        return self.shell_result(self.jobs.output(args["job_id"], args.get("tail_lines", 100)))
