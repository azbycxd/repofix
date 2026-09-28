"""Explicitly gated live connectivity smoke for the DeepSeek tool-call API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from repofix.agent import (  # noqa: E402
    AgentConfig,
    create_deepseek_client,
    request_chat_completion,
)


SMOKE_MESSAGES = [
    {
        "role": "system",
        "content": "This is a connectivity test. Follow the user request using a tool call.",
    },
    {"role": "user", "content": "Call submit now. Do not call bash."},
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Make exactly one real DeepSeek request.",
    )
    args = parser.parse_args()
    config = AgentConfig()
    if not args.execute:
        print("READY_FOR_PROVIDER_CONNECTIVITY_SMOKE=YES")
        print("PROVIDER_CALLS=0")
        return 0

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    client = create_deepseek_client(api_key, config)
    response = request_chat_completion(client, SMOKE_MESSAGES, config)
    message = response.choices[0].message
    tool_calls = message.tool_calls or []
    usage = response.usage
    if usage is None:
        usage_fields = None
    elif hasattr(usage, "model_dump"):
        usage_fields = usage.model_dump(exclude_none=False)
    else:
        usage_fields = {"repr": repr(usage)}

    result = {
        "provider_calls": 1,
        "model": response.model,
        "finish_reason": response.choices[0].finish_reason,
        "tool_calls": [call.function.name for call in tool_calls],
        "submit_returned": any(
            call.function.name == "submit" for call in tool_calls
        ),
        "usage_fields": usage_fields,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["submit_returned"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
