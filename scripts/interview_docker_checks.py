"""Actual Docker integration evidence (B), with no live model calls."""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from repofix.agent import RepoFixAgent
from repofix.env import DockerEnv
from repofix.harness.checkpoint import CheckpointStore
from repofix.harness.config import HarnessConfig
from repofix.harness.context import ContextManager
from repofix.harness.model import FakeModelClient
from repofix.harness.runtime import Runtime
from repofix.harness.state import RunState
from repofix.harness.tools.files import FileTools
from repofix.harness.workspace import RepoFiles


def main():
    out = ROOT / 'runs/interview-real-20261009/docker-mechanisms'
    out.mkdir(parents=True, exist_ok=False)
    specs = json.loads((out.parent / 'task_images.json').read_text())
    spec = next(row for row in specs if row['task_id'] == 'django__django-16429')
    records = []
    config = HarnessConfig.for_profile('v3')
    with DockerEnv(spec['task_id'], spec['image'], 'interview-mechanisms', sandbox_hardening=True) as env:
        assert env.container.attrs['HostConfig']['NetworkMode'] == 'none'
        env_info = env.execute("which python; python --version; git rev-parse HEAD")
        (out / 'environment.txt').write_text(env_info.output)
        files = RepoFiles(env)
        state = RunState(messages=[{'role':'system','content':'fixture'}, {'role':'user','content':'check'}])
        fake = FakeModelClient([])
        agent = RepoFixAgent(env, 'integration fixture', out / 'unused.jsonl', '', 'integration-only', config, fake)

        def check(name, function):
            started = time.monotonic()
            try:
                details = function()
                row = dict(name=name, passed=True, details=details)
            except Exception:
                row = dict(name=name, passed=False, error=traceback.format_exc())
            row['wall_seconds'] = time.monotonic() - started
            records.append(row)
            (out / 'results.json').write_text(json.dumps(records, indent=2))
            print(json.dumps(row), flush=True)

        fixture = '.repofix_interview_fixture.py'

        def edit_large():
            original = 'VALUE = 1\n' + '# padding for large payload\n' * 12000
            files.apply({fixture: original})
            tool = FileTools(files, state, config)
            tool.view(dict(path=fixture, start_line=1, end_line=2))
            first = tool.replace(dict(path=fixture, old_str='VALUE = 1', new_str='VALUE = 2'))
            assert files.read(fixture).startswith('VALUE = 2\n'), first.content
            second = tool.replace(dict(path=fixture, old_str='VALUE = 2', new_str='VALUE = 3'))
            assert files.read(fixture).startswith('VALUE = 3\n'), second.content
            rollback = tool.replace(dict(path=fixture, old_str='VALUE = 3', new_str='VALUE ='))
            assert rollback.metadata.get('syntax_rollbacks') == 1
            assert files.read(fixture).startswith('VALUE = 3\n')
            return dict(chars=len(original), consecutive_edit=True, syntax_rollback=True)

        def hooks():
            runtime = Runtime(agent, state)
            env.execute("printf 'import unittest\nclass Check(unittest.TestCase):\n def test_it(self): self.fail(\"expected failure\")\n' > /tmp/repofix_interview_test.py")
            command = 'cd /tmp && python -m unittest repofix_interview_test'
            result = runtime.execute(dict(id='fail', name='bash', arguments=json.dumps({'command':command})))
            assert result.exit_code == 1, result.content
            assert 'benign_exit' not in result.content
            assert state.last_validation_version == -1
            denied = runtime.execute(dict(id='submit', name='submit', arguments='{}'))
            assert not state.submitted
            assert state.submit_blocks == 1
            return dict(exit_code=result.exit_code, blocked_submit=state.submit_blocks,
                        output=result.content, submit_observation=denied.content)

        def context():
            config_small = HarnessConfig.for_profile('v3', context_window=4000, keep_recent_tool_results=0)
            text = 'CONTEXT_ARTIFACT_SENTINEL\n' + 'readback\n' * 4000
            state.messages += [dict(role='assistant',content=None,tool_calls=[dict(id='artifact',type='function',function=dict(name='bash',arguments='{}'))]),
                               dict(role='tool',tool_call_id='artifact',content=text)]
            manager = ContextManager(config_small, fake, env, out)
            event = manager.maybe_compact(state)
            assert event and event['masked_results'] == 1
            paths = list((out / 'context').glob('tool-*.txt'))
            assert len(paths) == 1
            result = env.execute('cat ' + manager.container_path(paths[0]))
            assert result.output == text and result.exit_code == 0
            return dict(chars=len(text), artifact_readback=True, event=event)

        def checkpoint():
            state.pending_calls = [dict(id='pending',name='bash',arguments=json.dumps({'command':'echo MUST_NOT_REPLAY'}))]
            state.step = 1
            store = CheckpointStore(out)
            files.apply({fixture:'VALUE = 42\n'})
            assert env.execute("printf 'untracked-before\n' > .repofix_interview_untracked.txt").exit_code == 0
            store.save(state, env)
            files.apply({fixture:'VALUE = 99\n'})
            assert env.execute("printf 'mutated\n' > .repofix_interview_untracked.txt").exit_code == 0
            restored = store.resume(env)
            assert files.read(fixture) == 'VALUE = 42\n'
            assert files.read('.repofix_interview_untracked.txt') == 'untracked-before\n'
            assert restored.messages[-1]['content'].startswith('[interrupted:')
            assert not restored.pending_calls
            return dict(snapshot_kind='docker', tracked_restored=True, untracked_restored=True,
                        pending_call_filled_without_execution=True,
                        limitation='Controlled store resume, not end-to-end model/CLI restart')

        check('large_edit_transport_consecutive_edit_and_rollback', edit_large)
        check('failed_validation_is_not_benign_and_blocks_submit', hooks)
        check('context_artifact_real_container_readback', context)
        check('workspace_snapshot_restore_and_pending_fill', checkpoint)
    print('LIVE_PROVIDER_CALLS=0')
    return 0 if all(row['passed'] for row in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
