"""One persisted wall-clock deadline, independent from tool/provider timeouts."""

import threading
import time
from dataclasses import replace


class TaskDeadline(TimeoutError):
    pass


class Deadline:
    def __init__(self, config, state):
        self.state = state
        self.enforcing = True
        facts = state.metadata.setdefault("v4", {})
        self.expires = facts.setdefault(
            "deadline_epoch", time.time() + config.task_deadline_seconds
        )

    def remaining(self):
        return max(0.0, self.expires - time.time())

    def check(self):
        if self.enforcing and self.remaining() <= 0:
            raise TaskDeadline("task wall-clock deadline reached")


class DeadlineModel:
    """Do not let SDK retry latency authorize late tool execution.

    A provider thread cannot be forcibly killed by Python; its eventual response
    is discarded. Unknown usage is explicitly recorded, never invented as zero.
    """

    def __init__(self, model, deadline):
        self.model, self.deadline = model, deadline

    def complete(self, messages, tools, config):
        self.deadline.check()
        remaining = self.deadline.remaining()
        bounded = replace(
            config, provider_timeout_seconds=min(config.provider_timeout_seconds, remaining)
        )
        value = {}

        def request():
            try:
                value["response"] = self.model.complete(messages, tools, bounded)
            except BaseException as exc:
                value["error"] = exc

        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        worker.join(remaining)
        if worker.is_alive():
            facts = self.deadline.state.metadata.setdefault("v4", {})
            facts["provider_usage_incomplete"] = True
            facts["unobserved_provider_requests"] = facts.get("unobserved_provider_requests", 0) + 1
            raise TaskDeadline("provider request exceeded total task deadline; usage is unknown")
        if "error" in value:
            raise value["error"]
        self.deadline.check()
        return value["response"]
