from dataclasses import dataclass
from typing import Callable
from concurrent.futures import ThreadPoolExecutor


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


def schema(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(required), "additionalProperties": False}}}


def schedule(calls, registry, execute, parallel=False, workers=4):
    """Yield in model order; a write is a barrier on BOTH sides."""
    offset = 0
    while offset < len(calls):
        end = offset
        while end < len(calls):
            spec = registry.specs.get(calls[end]["name"])
            if not spec or not spec.read_only:
                break
            end += 1
        if end == offset:
            yield calls[offset], execute(calls[offset]), 1
            offset += 1
            continue
        batch = calls[offset:end]
        if parallel and len(batch) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(execute, call) for call in batch]
                for call, future in zip(batch, futures):
                    yield call, future.result(), len(batch)
        else:
            for call in batch:
                yield call, execute(call), 1
        offset = end
