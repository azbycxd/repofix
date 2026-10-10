"""Child-process fault fixture: real loop/store/Docker; parent sends actual SIGKILL."""

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from repofix.agent import RepoFixAgent
from repofix.env import DockerEnv
from repofix.harness.checkpoint_v4 import V4CheckpointStore
from repofix.harness.config import HarnessConfig
from repofix.harness.model import FakeModelClient
from repofix.harness.validation import workspace_signature


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["run", "resume"])
    parser.add_argument("--phase", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    config = HarnessConfig.for_profile("v4", progress_monitor=False)
    with DockerEnv(
        "v4-kill-" + args.phase,
        args.image,
        args.folder.name + "-" + args.mode,
        sandbox_hardening=True,
    ) as env:
        env.v4_helpers = True
        if args.mode == "resume":
            store = V4CheckpointStore(args.folder)
            state = store.resume(env)
            evidence = {
                "state": asdict(state),
                "patch": env.get_diff(),
                "workspace_signature": workspace_signature(env),
                "manifest": store.loaded_manifest,
                "new_container": env.container.id,
            }
            (args.folder / "resumed.json").write_text(json.dumps(evidence, indent=2))
            agent = RepoFixAgent(
                env,
                "fixture",
                args.folder / "resume.jsonl",
                "",
                "fixture",
                config,
                FakeModelClient([{}, {}]),
            )
            agent.state = state
            agent.run()
            return
        commands = {
            "pre_dispatch": "printf SHOULD_NOT_RUN > /testbed/module.py",
            "post_edit": "printf 'VALUE = 2\\n' > /testbed/module.py",
            "mid_write": "printf 'VALUE = 2\\n' > /testbed/module.py; touch /tmp/v4-mid-write; "
            "sleep 60; printf 'VALUE = 3\\n' > /testbed/module.py",
            "active_job": "sleep 120",
        }
        kwargs = {"command": commands[args.phase]}
        if args.phase == "active_job":
            kwargs["run_in_background"] = True
        agent = RepoFixAgent(
            env,
            "fixture",
            args.folder / "original.jsonl",
            "",
            "fixture",
            config,
            FakeModelClient([{"calls": [("bash", kwargs)]}]),
        )
        (args.folder / "container.json").write_text(
            json.dumps({"id": env.container.id, "name": env.name})
        )
        original = V4CheckpointStore.save

        def save(store, state, target_env, reason="end_step"):
            event = original(store, state, target_env, reason)
            hit = (args.phase == "pre_dispatch" and reason == "pre_dispatch") or (
                args.phase in {"post_edit", "active_job"} and reason == "post_mutation"
            )
            if hit:
                (args.folder / "kill-ready.json").write_text(json.dumps(event))
                while True:
                    time.sleep(1)
            return event

        with patch.object(V4CheckpointStore, "save", save):
            agent.run()


if __name__ == "__main__":
    main()
