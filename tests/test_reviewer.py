from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from repofix.agent import AgentConfig, RepoFixAgent
from repofix.reviewer import (
    REVIEWER_SYSTEM_PROMPT,
    parse_reviewer_output,
    review_patch,
    reviewer_messages,
)


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                prompt_cache_hit_tokens=40,
                prompt_cache_miss_tokens=60,
                completion_tokens=3,
            ),
        )


class ScriptedCompletions:
    def __init__(self) -> None:
        self.agent_calls = 0
        self.reviewer_calls = 0

    @staticmethod
    def _usage():
        return SimpleNamespace(
            prompt_tokens=10,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=10,
            completion_tokens=2,
        )

    def create(self, **kwargs):
        if "tools" not in kwargs:
            self.reviewer_calls += 1
            message = SimpleNamespace(
                content="REJECT: add validation evidence",
                tool_calls=None,
            )
        else:
            self.agent_calls += 1
            message = SimpleNamespace(
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        id=f"submit-{self.agent_calls}",
                        function=SimpleNamespace(name="submit", arguments="{}"),
                    )
                ],
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=self._usage(),
        )


class FakeEnv:
    def read_repository_text_files(self):
        return {"django/example.py": "def example():\n    return 1\n"}

    def get_diff(self):
        return "diff --git a/django/example.py b/django/example.py\n"


class ReviewerTests(unittest.TestCase):
    def test_messages_have_only_required_independent_context(self) -> None:
        messages = reviewer_messages("issue", "diff", "test command", "passed")

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0], {"role": "system", "content": REVIEWER_SYSTEM_PROMPT})
        self.assertEqual(messages[1]["role"], "user")
        for value in ("issue", "diff", "test command", "passed"):
            self.assertIn(value, messages[1]["content"])

    def test_output_protocol(self) -> None:
        self.assertEqual(parse_reviewer_output("APPROVE"), ("APPROVE", None))
        self.assertEqual(
            parse_reviewer_output("REJECT: missing validation"),
            ("REJECT", "missing validation"),
        )
        for output in ("approve", "REJECT:", "APPROVE\nextra"):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    parse_reviewer_output(output)

    def test_review_is_one_call_without_tools_and_records_usage(self) -> None:
        completions = FakeCompletions("REJECT: assertion was weakened")
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        result = review_patch(
            client,
            AgentConfig(),
            "issue",
            "diff",
            "test command",
            "passed",
        )

        self.assertEqual(result.verdict, "REJECT")
        self.assertEqual(result.reject_reason, "assertion was weakened")
        self.assertEqual(result.prompt_tokens, 100)
        self.assertEqual(result.cache_hit_tokens, 40)
        self.assertEqual(result.completion_tokens, 3)
        self.assertNotIn("tools", completions.kwargs)
        self.assertEqual(completions.kwargs["temperature"], 0)
        self.assertEqual(completions.kwargs["reasoning_effort"], "none")

    def test_agent_reviewer_rejects_once_then_accepts_second_submit(self) -> None:
        completions = ScriptedCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with TemporaryDirectory() as directory:
            result = RepoFixAgent(
                env=FakeEnv(),
                issue="Fix example",
                trajectory_path=Path(directory) / "trace.jsonl",
                api_key="not-a-real-key",
                git_commit="test-commit",
                config=AgentConfig(max_steps=3, reviewer_enabled=True),
                client=client,
            ).run()

        self.assertTrue(result.submitted)
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.reviewer_calls, 1)
        self.assertEqual(result.reviewer_verdict, "REJECT")
        self.assertTrue(result.reviewer_returned)
        self.assertFalse(result.patch_changed_after_reject)
        self.assertEqual(completions.agent_calls, 2)
        self.assertEqual(completions.reviewer_calls, 1)


if __name__ == "__main__":
    unittest.main()
