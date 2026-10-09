"""Container-local background processes; host only polls JSON status."""
import json
import shlex
import time
import uuid

from ..workspace import run_python
from ..permissions import split_commands, unwrap

SHELL_ENV = {"PAGER": "cat", "GIT_PAGER": "cat", "NO_COLOR": "1", "TERM": "dumb", "PYTHONUNBUFFERED": "1"}


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
    def __init__(self, env):
        self.env = env
        self.jobs = {}

    def start(self, command):
        job_id = uuid.uuid4().hex[:16]
        job = {"id": job_id, "command": command, "start": time.monotonic(), "pid": None}
        if hasattr(self.env, "files"):
            job["result"] = self.env.execute(command)
        else:
            worker = '''import subprocess, pathlib, sys
command, stem = sys.argv[1:]
with open(stem + '.log', 'wb', buffering=0) as out:
    p = subprocess.run(['bash', '-lc', command], cwd='/testbed', stdout=out, stderr=subprocess.STDOUT)
pathlib.Path(stem + '.exit').write_text(str(p.returncode))
'''
            source = f'''import subprocess, pathlib, os, json
folder = pathlib.Path('/tmp/repofix_jobs'); folder.mkdir(mode=0o700, exist_ok=True)
stem = str(folder / {job_id!r})
environment = dict(os.environ); environment.update({SHELL_ENV!r})
with open(os.devnull, 'wb') as null:
    p = subprocess.Popen(['python', '-c', {worker!r}, {command!r}, stem], cwd='/testbed',
        env=environment, stdin=subprocess.DEVNULL, stdout=null, stderr=null, start_new_session=True)
print(json.dumps(p.pid))
'''
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
            code = job.get("killed", result.exit_code if not result.timed_out and elapsed >= result.latency_seconds else None)
            output = result.output
        else:
            source = f'''import pathlib, json
stem = pathlib.Path('/tmp/repofix_jobs') / {job_id!r}
log = pathlib.Path(str(stem) + '.log'); status = pathlib.Path(str(stem) + '.exit')
print(json.dumps({{'output': log.read_text(errors='replace') if log.exists() else '',
                  'code': int(status.read_text()) if status.exists() else None}}))
'''
            data = json.loads(run_python(self.env, source))
            output, code = data["output"], data["code"]
        if tail_lines is not None:
            output = "\n".join(output.splitlines()[-tail_lines:])
        return {"output": output, "exit_code": code, "duration": elapsed, "job_id": job_id,
                "still_running": code is None, "command": job["command"],
                "log_path": f"/tmp/repofix_jobs/{job_id}.log"}

    def wait(self, job_id, timeout):
        deadline = time.monotonic() + timeout
        while True:
            data = self.output(job_id)
            if not data["still_running"] or time.monotonic() >= deadline:
                return data
            time.sleep(min(.1, max(0, deadline - time.monotonic())))

    def kill(self, job_id):
        if job_id not in self.jobs:
            raise ValueError("unknown job_id")
        job = self.jobs[job_id]
        if hasattr(self.env, "files"):
            job["killed"] = 143
        else:
            source = f'''import os, signal, pathlib
try: os.killpg({job['pid']!r}, signal.SIGKILL)
except ProcessLookupError: pass
pathlib.Path('/tmp/repofix_jobs/{job_id}.exit').write_text('137')
'''
            run_python(self.env, source)
        return self.output(job_id)


def foreground(env, command, timeout):
    prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in SHELL_ENV.items())
    started = time.monotonic()
    result = env.execute(command if hasattr(env, "files") else prefix + " bash -lc " + shlex.quote(command), timeout)
    return {"output": result.output, "exit_code": result.exit_code, "duration": time.monotonic() - started,
            "command": command, "timed_out": result.timed_out, "still_running": False}
