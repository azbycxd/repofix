"""Inspect the actual Docker archive payload and privileged cleanup contract."""

import io
import tarfile
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from repofix.env import DockerEnv


@pytest.mark.parametrize("sandbox_user, mode", [(None, 0o600), ("1000:1000", 0o644)])
@pytest.mark.parametrize("kind", ["apply", "restore"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_patch_payload_permissions_and_root_cleanup(sandbox_user, mode, kind, interrupted):
    env = DockerEnv("fixture", "image", "run", sandbox_user=sandbox_user)
    env.client = MagicMock()
    env.container = MagicMock()
    env.container.exec_run.return_value.exit_code = 0
    path = f"/tmp/repofix_{kind}_{'a' * 32}.json"
    content = '{"a.py": "VALUE = 2\\n"}'
    context = pytest.raises(KeyboardInterrupt) if interrupted else nullcontext()
    with context:
        with env.temporary_patch_file(path, content):
            directory, data = env.container.put_archive.call_args.args
            assert directory == "/tmp"
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                (member,) = archive.getmembers()
                assert member.name == path.rsplit("/", 1)[1]
                assert member.mode == mode
                assert member.uid == member.gid == 0
                assert archive.extractfile(member).read().decode() == content
            env.container.exec_run.assert_not_called()
            if interrupted:
                raise KeyboardInterrupt()
    env.container.exec_run.assert_called_once_with(["rm", "-f", "--", path], user="root")


@pytest.mark.parametrize("path", ["/tmp/other.json", "/testbed/a.py", "/tmp/../etc/passwd"])
def test_patch_cleanup_rejects_unrelated_paths(path):
    env = DockerEnv("fixture", "image", "run", sandbox_user="1000")
    env.client, env.container = MagicMock(), MagicMock()
    with pytest.raises(ValueError, match="temporary patch path"):
        with env.temporary_patch_file(path, "{}"):
            pytest.fail("must not yield")
    env.container.put_archive.assert_not_called()
    env.container.exec_run.assert_not_called()


def test_ordinary_output_keeps_private_mode():
    env = DockerEnv("fixture", "image", "run", sandbox_user="1000")
    env.client, env.container = MagicMock(), MagicMock()
    env.write_text_file("/tmp/output.txt", "private")
    _, data = env.container.put_archive.call_args.args
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        assert archive.getmembers()[0].mode == 0o600
