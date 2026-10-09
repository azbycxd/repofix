from dataclasses import dataclass, fields
from repofix.agent import AgentConfig


@dataclass(frozen=True)
class HarnessConfig(AgentConfig):
    profile: str = "v1"
    parallel_readonly: bool = False
    hooks_enabled: bool = False
    hooks_file: str | None = None
    apply_patch_enabled: bool = False
    read_before_edit: bool = False
    background_shell: bool = False
    context_management: bool = False
    plan_tool: bool = False
    checkpointing: bool = False
    permissions_file: str | None = None
    permissions_enabled: bool = False
    sandbox_hardening: bool = False
    sandbox_user: str | None = None
    subagents: str = "none"
    task_kind: str = "bugfix"
    context_window: int = 64000
    compact_threshold: float = 0.75
    keep_recent_tool_results: int = 4
    evaluation_mode: bool = True
    permission_default: str = "allow"
    verification_patterns: tuple[str, ...] = ()
    verify_on_submit: bool = False
    readonly_workers: int = 4

    def __post_init__(self):
        if self.profile not in {"v1", "v3"} or self.task_kind not in {"bugfix", "feature"}:
            raise ValueError("invalid profile or task_kind")
        if self.subagents not in {"none", "explore", "verify", "both"}:
            raise ValueError("invalid subagents mode")
        if self.context_window < 1 or not 0 < self.compact_threshold <= 1 or self.keep_recent_tool_results < 0:
            raise ValueError("invalid context limits")
        if self.max_steps < 1 or self.max_cost_usd <= 0 or self.readonly_workers < 1:
            raise ValueError("invalid step/cost/worker limits")
        if self.profile == "v1" and (self.task_kind != "bugfix" or self.subagents != "none" or any((
            self.parallel_readonly, self.hooks_enabled, self.apply_patch_enabled, self.read_before_edit,
            self.background_shell, self.context_management, self.plan_tool, self.checkpointing,
            self.permissions_enabled, self.sandbox_hardening))):
            raise ValueError("v1 cannot enable V3 mechanisms")
        if self.profile == "v1":
            frozen = AgentConfig()
            if any(getattr(self, f.name) != getattr(frozen, f.name) for f in fields(AgentConfig)):
                raise ValueError("profile v1 model/tool/limit configuration is frozen; use v3 for overrides")

    @classmethod
    def for_profile(cls, profile="v1", **overrides):
        if profile not in {"v1", "v3"}:
            raise ValueError("profile must be v1 or v3")
        if profile == "v1" and overrides.get("task_kind", "bugfix") != "bugfix":
            raise ValueError("feature tasks require --profile v3")
        enabled = {name: True for name in (
            "parallel_readonly", "hooks_enabled", "apply_patch_enabled",
            "read_before_edit", "background_shell", "context_management",
            "plan_tool", "checkpointing", "sandbox_hardening",
            "permissions_enabled",
        )} if profile == "v3" else {}
        enabled.update(overrides)
        if profile == "v1" and any(enabled.get(name, False) for name in (
            "parallel_readonly", "hooks_enabled", "apply_patch_enabled", "read_before_edit",
            "background_shell", "context_management", "plan_tool", "checkpointing", "sandbox_hardening")):
            raise ValueError("v1 cannot enable v3 mechanisms")
        return cls(profile=profile, **enabled)

    def legacy_config(self):
        return AgentConfig(**{item.name: getattr(self, item.name) for item in fields(AgentConfig)})
