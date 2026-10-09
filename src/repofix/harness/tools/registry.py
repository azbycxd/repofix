from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: dict
    read_only: bool
    handler: Callable


class ToolRegistry:
    def __init__(self, specs=()):
        self.specs = {spec.name: spec for spec in specs}

    def add(self, spec):
        if spec.name in self.specs:
            raise ValueError(f"duplicate tool: {spec.name}")
        self.specs[spec.name] = spec

    @property
    def schemas(self):
        return [spec.schema for spec in self.specs.values()]

    def execute(self, name, args):
        if name not in self.specs:
            raise ValueError(f"unknown tool: {name}")
        return self.specs[name].handler(args)


def v1_registry(handler):
    from repofix.agent import TOOLS
    return ToolRegistry(ToolSpec(schema["function"]["name"], schema,
        schema["function"]["name"] in {"view", "search_code"}, handler) for schema in TOOLS)
