from __future__ import annotations

import unittest

from repofix.reproduction import (
    ReproductionTelemetry,
    reproduction_key,
    usable_validation_evidence,
)


class ReproductionTelemetryTests(unittest.TestCase):
    def test_reproduction_flips_after_production_edit(self) -> None:
        telemetry = ReproductionTelemetry()
        command = "python /tmp/repofix_repro.py"
        telemetry.observe_shell(2, command, 1, "AssertionError")
        telemetry.observe_changes(3, [("M", "django/forms/models.py")])
        telemetry.observe_shell(4, command, 0, "ok")

        self.assertTrue(telemetry.pre_fix_reproduced)
        self.assertTrue(telemetry.post_fix_repro_passed)
        self.assertTrue(telemetry.repro_flipped)
        self.assertEqual(telemetry.first_production_edit_step, 3)
        self.assertEqual(telemetry.pre_fix_repro_step, 2)
        self.assertEqual(telemetry.post_fix_repro_step, 4)

    def test_existing_test_and_search_attempts_are_counted(self) -> None:
        telemetry = ReproductionTelemetry()
        telemetry.observe_changes(5, [("M", "tests/example/test_case.py")])
        telemetry.observe_shell(6, "git log -5 -- path", 0, "history")
        telemetry.observe_shell(7, "pip download django -d /tmp", 1, "offline")

        self.assertTrue(telemetry.existing_test_modified)
        self.assertIsNone(telemetry.first_production_edit_step)
        self.assertEqual(telemetry.git_history_search_count, 1)
        self.assertEqual(telemetry.network_attempt_count, 1)

    def test_setup_failure_is_not_issue_reproduction(self) -> None:
        for output in (
            "ModuleNotFoundError: No module named 'anything'",
            "ImportError: cannot import name 'Thing' from 'example'",
        ):
            with self.subTest(output=output):
                telemetry = ReproductionTelemetry()
                telemetry.observe_shell(
                    1,
                    "python /tmp/repofix_repro.py",
                    1,
                    output,
                )
                self.assertFalse(telemetry.pre_fix_reproduced)

    def test_test_command_has_stable_key_across_output_pipes(self) -> None:
        first = "python tests/runtests.py app.Test.test_bug -v2 2>&1 | tail -20"
        second = "python tests/runtests.py app.Test.test_bug -v2"
        self.assertEqual(reproduction_key(first), reproduction_key(second))

    def test_reviewer_evidence_rejects_import_and_timeout_failures(self) -> None:
        command = "python /tmp/repofix_repro.py"
        self.assertFalse(
            usable_validation_evidence(command, "ImportError: broken", False)
        )
        self.assertFalse(usable_validation_evidence(command, "failed", True))
        self.assertTrue(usable_validation_evidence(command, "AssertionError", False))


if __name__ == "__main__":
    unittest.main()
