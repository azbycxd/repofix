"""Small model boundary; the fake never constructs a network client."""
from copy import deepcopy
from types import SimpleNamespace
from typing import Protocol, Any


class ModelClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], config: Any) -> Any: ...


class OpenAICompatibleClient:
    def __init__(self, client):
        self.client = client

    def complete(self, messages, tools, config):
        return self.client.chat.completions.create(
            model=config.model, messages=messages, tools=tools or None,
            temperature=config.temperature, reasoning_effort="none",
            extra_body={"thinking": {"type": config.thinking}},
            max_completion_tokens=config.max_completion_tokens,
            timeout=config.provider_timeout_seconds,
        )


class FakeModelClient:
    """Script items: content, calls=[(name, args)], usage, or an exception."""
    def __init__(self, script):
        self.script = iter(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        item = next(self.script)
        if isinstance(item, BaseException):
            raise item
        import json
        calls = [SimpleNamespace(
            id=f"call_{len(self.requests)}_{i}",
            function=SimpleNamespace(name=name, arguments=args if isinstance(args, str)
                                     else json.dumps(args)),
        ) for i, (name, args) in enumerate(item.get("calls", []))]
        usage = item.get("usage", {"prompt_tokens": 100, "completion_tokens": 10,
                                   "prompt_cache_hit_tokens": 20})
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=item.get("content"), tool_calls=calls),
                finish_reason=item.get("finish_reason", "tool_calls" if calls else "stop"))],
            usage=SimpleNamespace(**usage),
        )

    def complete(self, messages, tools, config):
        return OpenAICompatibleClient(self).complete(messages, tools, config)
