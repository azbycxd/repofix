from unittest.mock import MagicMock, patch
import pytest
from repofix.harness.permissions import PermissionPolicy, DEFAULT_RULES, split_commands
from repofix.harness.hooks import command_policy, Deny, Allow
from repofix.harness.config import HarnessConfig
from repofix.harness.state import RunState


@pytest.mark.parametrize("command", ["echo ok && curl example", "nohup timeout -k 2 5 env X=Y curl example", "env -i X=Y /usr/bin/curl example", "echo x\ngit push origin main", "echo x | wget example"])
def test_deny_compound_and_wrappers(command):
    assert PermissionPolicy(DEFAULT_RULES).decide(command).decision == "deny"


def test_quoted_separators_and_prefix_boundaries():
    assert len(split_commands("echo 'x;y|z' && echo \"a&&b\"")) == 2
    assert PermissionPolicy(DEFAULT_RULES).decide("echo 'curl; git push' ; echo done").decision == "allow"
    assert PermissionPolicy(DEFAULT_RULES).decide("curlish").decision == "allow"


def test_toml_ask_modes(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text('default = "deny"\n[[rules]]\ndecision = "ask"\nprefix = "pip install"\n')
    assert PermissionPolicy.load(path).decide("pip install pkg").decision == "ask"
    call = {"name": "bash", "args": {"command": "pip install pkg"}}
    assert isinstance(command_policy(call, RunState(), HarnessConfig.for_profile("v3")), Deny)
    assert isinstance(command_policy(call, RunState(), HarnessConfig.for_profile("v3", evaluation_mode=False), lambda _: True), Allow)


def test_container_hardening_parameters():
    from repofix.env import DockerEnv
    import docker
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("missing")
    client.containers.create.return_value.status = "running"
    with patch("repofix.env.docker.from_env", return_value=client):
        env = DockerEnv("task", "image", "run", sandbox_hardening=True)
        env.start()
        kwargs = client.containers.create.call_args.kwargs
        assert kwargs["network_mode"] == "none" and kwargs["cap_drop"] == ["ALL"]
        assert kwargs["security_opt"] == ["no-new-privileges"]
        assert kwargs["pids_limit"] == 512 and kwargs["mem_limit"] == "4g"
        assert kwargs["nano_cpus"] == 2_000_000_000
        env.close()


def test_redirections_are_not_commands():
    assert split_commands("pytest 2>&1 | tail -5") == [["pytest"], ["tail", "-5"]]
    policy = PermissionPolicy([
        {"decision": "allow", "prefix": "pytest"},
        {"decision": "allow", "prefix": "tail"},
    ], default="deny")
    assert policy.decide("pytest 2>&1 | tail -5").decision == "allow"
    for suffix in ["> log", "2>> log", "&> log", ">&2", "< input", '> "a | b"']:
        assert split_commands("pytest " + suffix) == [["pytest"]]


@pytest.mark.parametrize("command", [
    "echo $(git push)", "echo `git push`", 'echo "$(git push)"',
    "echo $(echo $(git push))", "echo x >$(git push)",
])
def test_command_substitution_is_checked(command):
    assert PermissionPolicy(DEFAULT_RULES).decide(command).decision == "deny"


def test_quoted_literal_substitution_is_not_executed():
    assert PermissionPolicy(DEFAULT_RULES).decide("echo '$(git push)' '`git push`'").decision == "allow"
