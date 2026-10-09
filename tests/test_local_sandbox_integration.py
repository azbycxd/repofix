from __future__ import annotations

import os
import tempfile
import unittest
import pytest
from pathlib import Path

from repofix.env import DockerEnv
from repofix.python_sandbox import PythonSandboxImage

pytestmark = pytest.mark.docker


@unittest.skipUnless(
    os.environ.get("REPOFIX_DOCKER_INTEGRATION") == "1",
    "set REPOFIX_DOCKER_INTEGRATION=1 to build the local Python sandbox",
)
class LocalSandboxIntegrationTests(unittest.TestCase):
    def test_builds_project_and_runs_with_network_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / "pyproject.toml").write_text(
                """[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "repofix-sandbox-fixture"
version = "0.0.0"
""",
                encoding="utf-8",
            )
            (repository / "sandbox_fixture.py").write_text(
                "VALUE = 'installed'\n", encoding="utf-8"
            )

            with PythonSandboxImage(repository, "integration-test") as build:
                with DockerEnv(
                    "sandbox-fixture", build.image, "integration-test"
                ) as env:
                    result = env.execute(
                        "cd /tmp && pwd && python --version && "
                        "python -c \"import sandbox_fixture; print(sandbox_fixture.VALUE)\""
                    )
                    edit = env.str_replace_file(
                        "/testbed/sandbox_fixture.py",
                        "VALUE = 'installed'",
                        "VALUE = 'edited'",
                    )
                    edited_result = env.execute(
                        "cd /tmp && python -c \"import sandbox_fixture; "
                        "print(sandbox_fixture.VALUE)\""
                    )
                    assert env.container is not None
                    env.container.reload()
                    network_mode = env.container.attrs["HostConfig"]["NetworkMode"]

            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.timed_out)
            self.assertIn("/tmp", result.output)
            self.assertIn("Python 3.12", result.output)
            self.assertIn("installed", result.output)
            self.assertTrue(edit.success)
            self.assertEqual(edited_result.exit_code, 0)
            self.assertIn("edited", edited_result.output)
            self.assertEqual(network_mode, "none")


if __name__ == "__main__":
    unittest.main()
