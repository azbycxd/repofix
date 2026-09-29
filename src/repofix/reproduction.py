"""Lightweight behavior telemetry for the reproduction-first workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Sequence


_GIT_HISTORY_RE = re.compile(
    r"(?:^|[;&|]\s*|\s)git\s+(?:log|show|blame|reflog|rev-list)\b"
)
_NETWORK_RE = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:curl|wget)\b"
    r"|\bgit\s+(?:clone|fetch|pull)\b"
    r"|\b(?:python\s+-m\s+)?pip\s+(?:download|install)\b"
    r"|\b(?:requests\.(?:get|post)|urlopen)\s*\("
    r"|https?://",
    re.IGNORECASE,
)
_REPRO_FILE_RE = re.compile(
    r"(/tmp/[^\s'\"]*(?:repro|regression)[^\s'\"]*\.py)", re.IGNORECASE
)
_TEST_COMMAND_RE = re.compile(
    r"\b(?:pytest|py\.test|tox|unittest)\b"
    r"|\b(?:python\S*\s+)?tests/runtests\.py\b"
    r"|\bmanage\.py\s+test\b",
    re.IGNORECASE,
)
_FAILURE_OUTPUT_RE = re.compile(
    r"(?m)^(?:FAILED(?:\s|\()|FAIL:|ERROR:|Traceback \(most recent call last\):)"
    r"|\bAssertionError\b"
)
_SETUP_FAILURE_RE = re.compile(
    r"ModuleNotFoundError: No module named ['\"](?:test_sqlite|tests)['\"]"
)


def is_test_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return (
        "tests" in candidate.parts
        or candidate.name.startswith("test_")
        or candidate.name.endswith("_test.py")
    )


def reproduction_key(command: str) -> str | None:
    file_match = _REPRO_FILE_RE.search(command)
    if file_match:
        return f"file:{file_match.group(1)}"
    test_match = _TEST_COMMAND_RE.search(command)
    if not test_match:
        return None
    test_command = command[test_match.start() :]
    test_command = re.split(r"\s*(?:2?>|\|)\s*", test_command, maxsplit=1)[0]
    return "test:" + " ".join(test_command.split())


def reproduction_failed(exit_code: int | None, output: str) -> bool:
    if _SETUP_FAILURE_RE.search(output):
        return False
    return exit_code not in (0, None) or bool(_FAILURE_OUTPUT_RE.search(output))


@dataclass
class ReproductionTelemetry:
    pre_fix_reproduced: bool = False
    post_fix_repro_passed: bool = False
    repro_flipped: bool = False
    first_production_edit_step: int | None = None
    existing_test_modified: bool = False
    git_history_search_count: int = 0
    network_attempt_count: int = 0
    pre_fix_repro_step: int | None = None
    post_fix_repro_step: int | None = None
    reproduction_key_used: str | None = None
    telemetry_errors: int = 0
    _failed_reproduction_keys: set[str] = field(default_factory=set, repr=False)

    def observe_changes(
        self, step: int, changes: Sequence[tuple[str, str]]
    ) -> None:
        for status, path in changes:
            if status.startswith("M") and is_test_path(path):
                self.existing_test_modified = True
            if not is_test_path(path) and self.first_production_edit_step is None:
                self.first_production_edit_step = step

    def observe_shell(
        self,
        step: int,
        command: str,
        exit_code: int | None,
        output: str,
    ) -> None:
        self.git_history_search_count += int(bool(_GIT_HISTORY_RE.search(command)))
        self.network_attempt_count += int(bool(_NETWORK_RE.search(command)))

        key = reproduction_key(command)
        if key is None:
            return
        failed = reproduction_failed(exit_code, output)
        if self.first_production_edit_step is None and failed:
            self._failed_reproduction_keys.add(key)
            if not self.pre_fix_reproduced:
                self.pre_fix_reproduced = True
                self.pre_fix_repro_step = step
                self.reproduction_key_used = key
            return
        if (
            self.first_production_edit_step is not None
            and key in self._failed_reproduction_keys
            and exit_code == 0
            and not failed
        ):
            self.post_fix_repro_passed = True
            self.repro_flipped = True
            self.post_fix_repro_step = step
            self.reproduction_key_used = key

    def metrics(self) -> dict[str, object]:
        return {
            "PRE_FIX_REPRODUCED": self.pre_fix_reproduced,
            "POST_FIX_REPRO_PASSED": self.post_fix_repro_passed,
            "REPRO_FLIPPED": self.repro_flipped,
            "FIRST_PRODUCTION_EDIT_STEP": self.first_production_edit_step,
            "EXISTING_TEST_MODIFIED": self.existing_test_modified,
            "GIT_HISTORY_SEARCH_COUNT": self.git_history_search_count,
            "NETWORK_ATTEMPT_COUNT": self.network_attempt_count,
            "PRE_FIX_REPRO_STEP": self.pre_fix_repro_step,
            "POST_FIX_REPRO_STEP": self.post_fix_repro_step,
            "REPRODUCTION_KEY": self.reproduction_key_used,
            "TELEMETRY_ERRORS": self.telemetry_errors,
        }
