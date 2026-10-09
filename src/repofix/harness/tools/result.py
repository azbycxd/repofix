"""Result shared by tools without importing the runtime assembler."""

from dataclasses import dataclass, field


@dataclass
class ToolResult:
    content: str
    exit_code: int | None = None
    metadata: dict = field(default_factory=dict)
