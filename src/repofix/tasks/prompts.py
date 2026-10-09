from repofix.agent import SYSTEM_PROMPT

FEATURE_PROMPT = """You are a coding agent implementing a feature in /testbed.
Read the repository first and follow its conventions. Use update_plan to record
the work. Write or find executable acceptance tests that demonstrate the requested
behavior, implement the feature (including new files when necessary), then run
the relevant tests and existing tests before submit. Do not weaken existing tests
to fit an implementation. Keep temporary experiments in /tmp. The runtime has no
network access. Do not look for an official fix in network or Git history."""


def task_messages(kind, prompt):
    if kind not in {"bugfix", "feature"}:
        raise ValueError("unknown task kind")
    return [{"role": "system", "content": SYSTEM_PROMPT if kind == "bugfix" else FEATURE_PROMPT},
            {"role": "user", "content": ("Fix this issue in /testbed:\n\n" if kind == "bugfix" else "Implement this task in /testbed:\n\n") + prompt}]
