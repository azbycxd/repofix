"""Serializable state shared by the loop and optional harness mechanisms."""
from dataclasses import dataclass, field


@dataclass
class Budget:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    estimated_cost: float = 0.0
    provider_calls: int = 0
    cache_hit_available: bool = True


@dataclass
class RunState:
    messages: list[dict] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    step: int = 0
    pending_calls: list[dict] = field(default_factory=list)
    file_reads: dict[str, str] = field(default_factory=dict)
    termination: str | None = None
    submitted: bool = False
    counters: dict[str, int] = field(default_factory=dict)
    last_validation: dict | None = None
    workspace_version: int = 0
    last_validation_version: int = -1
    submit_blocks: int = 0
    events: list[dict] = field(default_factory=list)
    depth: int = 0
    metadata: dict = field(default_factory=dict)

    def count(self, name, amount=1):
        self.counters[name] = self.counters.get(name, 0) + amount


class LegacyState:
    """Internal v1 locals container. V1 does not checkpoint or compact."""
