"""Isolated, append-only real evaluation driver; never changes Agent behavior."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('/mnt/d/workspace/repo-agent-project/repofix')
OUT = ROOT / 'runs/interview-real-20261009'
PYTHON = '/home/jiusi/venvs/repofix/bin/python'
sys.path.insert(0, str(ROOT / 'src'))

from repofix.core import TrajectoryWriter
from repofix.harness.config import HarnessConfig


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def load_key():
    return os.environ.get('DEEPSEEK_API_KEY') or dotenv_values(SOURCE / '.env').get('DEEPSEEK_API_KEY') or ''


def protected(root):
    names = ['FINAL_CONFIG.md', 'tasks.txt', 'holdout.txt']
    names += capture(['git', 'ls-files', 'dev_artifacts'], root).splitlines()
    return {name: sha(root / name) for name in names}


def run_command(label, args, *, provider=False, extra_env=None, timeout=1800):
    folder = OUT / 'commands' / label
    folder.mkdir(parents=True, exist_ok=False)
    key = load_key()
    redactor = TrajectoryWriter(folder / 'unused.jsonl', secrets=[key])
    env = dict(os.environ)
    env.pop('DEEPSEEK_API_KEY', None)
    env['PYTHONPATH'] = str(ROOT / 'src')
    env['PYTHONUNBUFFERED'] = '1'
    if provider:
        if not key:
            raise RuntimeError('DeepSeek key unavailable')
        env['DEEPSEEK_API_KEY'] = key
    env.update(extra_env or {})
    record = dict(command=args, cwd=str(ROOT), started=now(), provider_enabled=provider)
    write_json(folder / 'command.json', record)
    started = time.monotonic()
    print(json.dumps(dict(event='start', label=label, at=record['started'])), flush=True)
    process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, errors='replace',
                               start_new_session=True)
    record['pid'] = process.pid
    write_json(folder / 'command.json', record)

    def drain(pipe, path):
        with path.open('w', encoding='utf-8') as handle:
            for line in pipe:
                handle.write(redactor._redact_text(line))
                handle.flush()

    threads = [threading.Thread(target=drain, args=(process.stdout, folder / 'stdout.log')),
               threading.Thread(target=drain, args=(process.stderr, folder / 'stderr.log'))]
    for thread in threads:
        thread.start()
    try:
        code = process.wait(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            code = process.wait()
    for thread in threads:
        thread.join()
    record.update(ended=now(), exit_code=code, timed_out=timed_out,
                  wall_seconds=time.monotonic() - started)
    write_json(folder / 'command.json', record)
    print(json.dumps(dict(event='end', label=label, exit_code=code,
                          wall_seconds=record['wall_seconds'])), flush=True)
    return record


def prepare():
    if (OUT / 'plan.json').exists():
        raise RuntimeError('Plan already frozen; refusing overwrite')
    devs = (ROOT / 'tasks.txt').read_text().split()
    holdout = set((ROOT / 'holdout.txt').read_text().split())
    chosen = devs[:3]
    assert len(chosen) == len(set(chosen)) == 3 and not holdout.intersection(chosen)
    config = {p: asdict(HarnessConfig.for_profile(p)) for p in ('v1', 'v3')}
    plan = dict(created=now(), base_sha=capture(['git', 'rev-parse', 'HEAD']),
                branch=capture(['git', 'branch', '--show-current']), tasks=chosen,
                profiles=['v1', 'v3'], repeats=1,
                selection_reason='Before model calls: first 3 ordered DEV, preserve time for independent Judge within ~3h / estimated $3.',
                order=[dict(task_id=t, profile=p) for t in chosen for p in ('v1', 'v3')],
                config=config, budget_usd=3.0, wall_budget_seconds=10800,
                no_holdout=True, source_status=capture(['git', 'status', '--porcelain'], SOURCE),
                source_sha=capture(['git', 'rev-parse', 'HEAD'], SOURCE),
                protected_source=protected(SOURCE), protected_eval=protected(ROOT),
                key_present=bool(load_key()), python=sys.version,
                packages={p: importlib.metadata.version(p) for p in ('swebench','datasets','openai','docker','pytest')})
    write_json(OUT / 'plan.json', plan)
    print(json.dumps({k: plan[k] for k in ('created','base_sha','branch','tasks','key_present','packages')}, ensure_ascii=False))
    run_command('environment', ['bash','-lc', 'pwd; which python; python --version; git status --porcelain; git rev-parse HEAD; git branch --show-current; git remote -v; docker version; docker system df; df -h / /mnt/d'],
                extra_env={'PATH': str(Path(PYTHON).parent) + ':' + os.environ['PATH']}, timeout=120)


def checks():
    for label, args, extra in (
        ('pytest-offline', [PYTHON,'-m','pytest','-q'], {}),
        ('pytest-v1-golden', [PYTHON,'-m','pytest','-q','tests/test_v1_golden.py'], {}),
        ('pytest-docker', [PYTHON,'-m','pytest','-q','-m','docker','--docker'], {'REPOFIX_DOCKER_INTEGRATION':'1'}),
        ('agent-help', [PYTHON,'scripts/run_agent.py','--help'], {}),
        ('judge-help', [PYTHON,'-m','swebench.harness.run_evaluation','--help'], {}),
    ):
        run_command(label, args, extra_env=extra, timeout=900)


def images():
    from datasets import load_dataset
    from swebench.harness.utils import make_test_spec
    plan = json.loads((OUT / 'plan.json').read_text())
    selected = set(plan['tasks'])
    images = []
    for row in load_dataset('SWE-bench/SWE-bench_Verified', split='test'):
        if row['instance_id'] in selected:
            spec = make_test_spec(row)
            images.append(dict(task_id=row['instance_id'], base_commit=row['base_commit'], image=spec.image))
    assert {r['task_id'] for r in images} == selected
    write_json(OUT / 'task_images.json', images)
    for item in images:
        run_command('pull-' + item['task_id'], ['docker','pull',item['image']], timeout=900)
    run_command('pull-python', ['docker','pull','python:3.12-slim'], timeout=300)


def models():
    plan = json.loads((OUT / 'plan.json').read_text())
    assert load_key(), 'No API key'
    assert protected(ROOT) == plan['protected_eval']
    total = 0.0
    for item in plan['order']:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(plan['created'])).total_seconds()
        if elapsed > 9000:
            write_json(OUT / 'time_stop.json', dict(at=now(), elapsed_seconds=elapsed,
                                                 reason='Reserve remaining time for Judge and report'))
            break
        if total >= plan['budget_usd']:
            write_json(OUT / 'budget_stop.json', dict(at=now(), estimated_usd=total))
            break
        task, profile = item['task_id'], item['profile']
        run_id = f'interview-real-20261009-{profile}-{task}'
        output = OUT / profile / task
        record = run_command(f'agent-{profile}-{task}',
                             [PYTHON,'scripts/run_agent.py','--profile',profile,'--instance-id',task,
                              '--run-id',run_id,'--output-dir',str(output)], provider=True, timeout=2400)
        result_path = output / f'{task}.result.json'
        if result_path.exists():
            summary = json.loads(result_path.read_text())
            total += summary.get('max_estimated_cost_usd') or 0.0
            print(json.dumps(dict(task=task,profile=profile,status=summary.get('status'),
                                  steps=summary.get('steps'),estimated_total_usd=total)), flush=True)
        else:
            print(json.dumps(dict(task=task,profile=profile,status='NO_RESULT',exit_code=record['exit_code'])),flush=True)


def bridge():
    """Use WSL registry connectivity without changing daemon proxy or image tags."""
    skopeo = ROOT / '.cache/image-tools/unpack/usr/bin/skopeo'
    extra = {'LD_LIBRARY_PATH': str(ROOT / '.cache/image-tools/unpack/usr/lib/x86_64-linux-gnu')}
    images = json.loads((OUT / 'task_images.json').read_text())
    images.insert(0, dict(task_id='python', image='python:3.12-slim'))
    for item in images:
        image = item['image']
        run_command('registry-manifest-' + item['task_id'],
                    [str(skopeo),'inspect','docker://docker.io/' + ('library/' if '/' not in image else '') + image],
                    extra_env=extra, timeout=120)
        result = run_command('registry-copy-' + item['task_id'],
                    [str(skopeo),'--insecure-policy','copy','--override-os','linux','--override-arch','amd64',
                     'docker://docker.io/' + ('library/' if '/' not in image else '') + image,
                     'docker-daemon:' + image], extra_env=extra, timeout=1200)
        if result['exit_code']:
            print('REGISTRY_BRIDGE_FAILED', flush=True)
            break


def bridge_retry():
    skopeo = ROOT / '.cache/image-tools/unpack/usr/bin/skopeo'
    extra = {'LD_LIBRARY_PATH': str(ROOT / '.cache/image-tools/unpack/usr/lib/x86_64-linux-gnu')}
    images = json.loads((OUT / 'task_images.json').read_text())
    # One infrastructure retry only; no Agent attempt has taken place.
    images.insert(0, dict(task_id='python', image='python:3.12-slim'))
    for item in images:
        image = item['image']
        record = run_command('registry-copy-retry-' + item['task_id'],
            [str(skopeo),'--insecure-policy','copy','--override-os','linux','--override-arch','amd64',
             'docker://docker.io/' + ('library/' if '/' not in image else '') + image,
             'docker-daemon:' + image], extra_env=extra, timeout=1200)
        if record['exit_code']:
            break


def proxy_transport():
    images = json.loads((OUT / 'task_images.json').read_text())
    images.insert(0, dict(task_id='python', image='python:3.12-slim'))
    for item in images:
        record = run_command('verified-transfer-v2-' + item['task_id'],
            [PYTHON,'scripts/interview_fetch_image.py',item['image']], timeout=1500)
        if record['exit_code']:
            break


def docker_recheck():
    run_command('pytest-docker-images-ready', [PYTHON,'-m','pytest','-q','-m','docker','--docker'],
                extra_env={'REPOFIX_DOCKER_INTEGRATION':'1'}, timeout=900)
    run_command('docker-mechanisms', [PYTHON,'scripts/interview_docker_checks.py'], timeout=600)


def docker_tests():
    run_command('pytest-docker-images-ready', [PYTHON,'-m','pytest','-q','-m','docker','--docker'],
                extra_env={'REPOFIX_DOCKER_INTEGRATION':'1'}, timeout=600)


def mechanisms():
    run_command('docker-mechanisms', [PYTHON,'scripts/interview_docker_checks.py'], timeout=600)


def sanity():
    from repofix.env import DockerEnv
    from interview_fetch_image import request
    images = json.loads((OUT / 'task_images.json').read_text())
    records = []
    for row in images:
        with DockerEnv(row['task_id'], row['image'], 'interview-sanity', sandbox_hardening=True) as env:
            commit = env.execute('git rev-parse HEAD')
            assert commit.exit_code == 0
            tree = env.execute('git rev-parse HEAD^{tree}')
            assert tree.exit_code == 0
            # Official image HEAD may differ from the dataset base. Validate source-tree
            # identity against the public upstream commit, not commit metadata.
            upstream = json.loads(request('https://api.github.com/repos/django/django/git/commits/' + row['base_commit']))
            upstream_tree = upstream['tree']['sha']
            details = env.execute('pwd; which python; python --version; git status --short; '
                                  'python -c "import django; print(django.__file__)"')
            assert details.exit_code == 0
            env.container.reload()
            network = env.container.attrs['HostConfig']['NetworkMode']
            assert network == 'none'
            records.append(dict(**row, output=details.output, network=network,
                                image_head=commit.output.strip(), image_tree=tree.output.strip(),
                                upstream_tree=upstream_tree,
                                tree_matches_upstream=tree.output.strip()==upstream_tree,
                                image_id=env.container.image.id))
            print(json.dumps(records[-1]), flush=True)
            write_json(OUT / 'docker_sanity.json', records)
            assert tree.output.strip() == upstream_tree, 'Official image source tree differs from upstream base'
    write_json(OUT / 'docker_sanity.json', records)


def sanity_recorded():
    run_command('docker-sanity-tree-verified', [PYTHON,'scripts/interview_eval.py','sanity'], timeout=300)


def sanity_local():
    """Record the exact official-image baseline used by both profiles and Judge."""
    from repofix.env import DockerEnv
    rows = []
    for item in json.loads((OUT / 'task_images.json').read_text()):
        with DockerEnv(item['task_id'], item['image'], 'interview-baseline-check', sandbox_hardening=True) as env:
            head = env.execute('git rev-parse HEAD')
            tree = env.execute('git rev-parse HEAD^{tree}')
            status = env.execute('git status --porcelain')
            info = env.execute('pwd; which python; python --version; '
                               'python -c "import django; print(django.__file__)"')
            assert all(r.exit_code == 0 and not r.timed_out for r in (head,tree,status,info))
            assert not status.output.strip(), 'Official baseline has dirty files'
            env.container.reload()
            assert env.container.attrs['HostConfig']['NetworkMode'] == 'none'
            row = dict(**item, image_head=head.output.strip(), image_tree=tree.output.strip(),
                       image_id=env.container.image.id, clean=True, network='none', output=info.output,
                       upstream_tree_verification='NOT_COMPLETED: public GitHub metadata connection timeout; '
                       'both profiles and Judge use identical official image and actual Git tree')
            rows.append(row)
            print(json.dumps(row), flush=True)
    write_json(OUT / 'docker_sanity.json', rows)


def baseline_check():
    run_command('docker-official-baselines', [PYTHON,'scripts/interview_eval.py','sanity_local'], timeout=180)


def judge():
    plan = json.loads((OUT / 'plan.json').read_text())
    holdouts = set((ROOT / 'holdout.txt').read_text().split())
    for profile in plan['profiles']:
        predictions = []
        for task in plan['tasks']:
            path = OUT / profile / task / 'predictions.json'
            if not path.exists():
                raise RuntimeError(f'Missing prediction for {profile}/{task}; do not invent patches')
            predictions.extend(json.loads(path.read_text()))
        ids = [row['instance_id'] for row in predictions]
        assert ids == plan['tasks'] and len(ids) == len(set(ids))
        assert not holdouts.intersection(ids)
        folder = OUT / 'judge' / profile
        folder.mkdir(parents=True, exist_ok=False)
        prediction_path = folder / 'predictions.json'
        write_json(prediction_path, predictions)
        run_command('judge-' + profile, [PYTHON,'-m','swebench.harness.run_evaluation',
            '--dataset_name','SWE-bench/SWE-bench_Verified','--split','test',
            '--predictions_path',str(prediction_path),'--run_id','interview-real-20261009-' + profile,
            '--max_workers','1','--timeout','900','--report_dir',str(folder),
            '--instance_ids',*plan['tasks']], timeout=3000)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare','checks','images','bridge','bridge_retry','proxy_transport','docker_recheck','docker_tests','mechanisms','sanity','sanity_recorded','sanity_local','baseline_check','models','judge'])
    args = parser.parse_args()
    globals()[args.phase]()


if __name__ == '__main__':
    main()
