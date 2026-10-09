from dataclasses import dataclass, fields
from repofix.agent import AgentConfig


@dataclass(frozen=True)
class HarnessConfig(AgentConfig):
    profile: str = "v1"
    parallel_readonly: bool = False
    hooks_enabled: bool = False
    apply_patch_enabled: bool = False
    read_before_edit: bool = False
    background_shell: bool = False
    context_management: bool = False
    plan_tool: bool = False
    checkpointing: bool = False
    permissions_file: str | None = None
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

    @classmethod
    def for_profile(cls, profile="v1", **overrides):
        if profile not in {"v1", "v3"}:
            raise ValueError("profile must be v1 or v3")
        enabled = {name: True for name in (
            "parallel_readonly", "hooks_enabled", "apply_patch_enabled",
            "read_before_edit", "background_shell", "context_management",
            "plan_tool", "checkpointing", "sandbox_hardening",
        )} if profile == "v3" else {}
        enabled.update(overrides)
        if profile == "v1" and any(enabled.get(name, False) for name in (
            "parallel_readonly", "hooks_enabled", "apply_patch_enabled", "read_before_edit",
            "background_shell", "context_management", "plan_tool", "checkpointing", "sandbox_hardening")):
            raise ValueError("v1 cannot enable v3 mechanisms")
        return cls(profile=profile, **enabled)

    def legacy_config(self):
        return AgentConfig(**{item.name: getattr(self, item.name) for item in fields(AgentConfig)})
