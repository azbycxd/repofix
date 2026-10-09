"""Derive comparison CSV/manifest from recorded runs, never from hand-filled scores."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from interview_eval import OUT, ROOT, SOURCE, capture, load_key, now, protected, sha, write_json

DOCS = ROOT / 'docs/interview/20261009-real'


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def csv_write(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    previous_manifest = read_json(DOCS / 'evidence_manifest.json')
    if previous_manifest:
        history = OUT / 'audit-history'
        history.mkdir(exist_ok=True)
        write_json(history / f'{len(list(history.glob("*.json"))) + 1:04d}.json',
                   dict(at=previous_manifest['created'], audit=previous_manifest['audit']))
    plan = read_json(OUT / 'plan.json')
    rows, evidence, cases = [], [], []
    for item in plan['order']:
        task, profile = item['task_id'], item['profile']
        folder = OUT / profile / task
        result_path = folder / f'{task}.result.json'
        result = read_json(result_path, {})
        trace = ROOT / result.get('trajectory_path', str(folder / (task + '/trajectory.jsonl' if profile == 'v3' else task + '.jsonl')))
        trajectory = records(trace)
        config = next((r for r in trajectory if r['type'] == 'config'), {})
        summary = next((r for r in reversed(trajectory) if r['type'] == 'summary'), {})
        steps = [r for r in trajectory if r['type'] == 'step']
        errors = [r for r in trajectory if r['type'] in ('fatal_error','provider_error','runner_error')]
        command = read_json(OUT / 'commands' / f'agent-{profile}-{task}' / 'command.json', {})
        judge_path = ROOT / f'logs/run_evaluation/interview-real-20261009-{profile}/deepseek-flash/{task}/report.json'
        judge = read_json(judge_path, {}).get(task)
        config_value = config.get('config', plan['config'][profile])
        config_hash = hashlib.sha256(json.dumps(config_value, sort_keys=True).encode()).hexdigest()
        provider = bool(steps) or any(r['type'] in ('fatal_error','provider_error') for r in errors)
        # A completed model step implies that this runner's DockerEnv and index
        # have actually been entered, unlike a config synthesized on startup error.
        docker = bool(steps) or result.get('index_chunk_count',0) > 0
        row = dict(task_id=task, profile=profile, repeat=1,
            git_sha=config.get('git_commit'), model=config_value.get('model'),
            config_sha256=config_hash, seed='not_set; temperature=0',
            run_id=f'interview-real-20261009-{profile}-{task}',
            real_provider=provider, real_docker=docker,
            provider_response_received=bool(steps),
            judge_status='JUDGED' if judge is not None else 'NOT_JUDGED',
            resolved=judge.get('resolved') if judge is not None else None,
            submitted=result.get('submitted'), termination_reason=result.get('status','NOT_RUN'),
            steps=result.get('steps'), tool_calls=result.get('tool_calls'),
            provider_calls=result.get('provider_calls'),
            prompt_tokens=result.get('prompt_tokens'), completion_tokens=result.get('completion_tokens'),
            cache_hit_tokens=result.get('cache_hit_tokens'),
            estimated_usd=result.get('max_estimated_cost_usd'),
            agent_wall_seconds=result.get('wall_time_seconds'),
            agent_process_wall_seconds=command.get('wall_seconds'),
            judge_wall_seconds=None,  # Official runner only records aggregate command wall time here.
            compactions=summary.get('compactions',0 if trajectory else None),
            subagent_calls=summary.get('subagent_calls',0 if trajectory else None),
            hook_blocks=summary.get('hook_blocks',0 if trajectory else None),
            checkpoints=len(list(trace.parent.glob('checkpoints/step-*.json'))),
            search_calls=result.get('search_calls'), truncations=result.get('truncations'),
            patch_nonempty=result.get('patch_nonempty'),
            evaluation_patch_nonempty=result.get('evaluation_patch_nonempty'),
            pre_fix_reproduced_telemetry=result.get('PRE_FIX_REPRODUCED'),
            repro_flipped_telemetry=result.get('REPRO_FLIPPED'),
            first_production_edit_step=result.get('FIRST_PRODUCTION_EDIT_STEP'),
            evidence_path=str(trace.relative_to(ROOT)) if trace.exists() else None,
            result_path=str(result_path.relative_to(ROOT)) if result_path.exists() else None,
            judge_path=str(judge_path.relative_to(ROOT)) if judge_path.exists() else None)
        rows.append(row)
        for p in (trace, result_path, judge_path, folder / f'{task}.patch', folder / f'{task}.evaluation.patch', folder / 'predictions.json'):
            if p.exists():
                evidence.append(dict(path=str(p.relative_to(ROOT)), sha256=sha(p), size_bytes=p.stat().st_size))
        tool_counts = Counter(c.get('name') or c.get('function',{}).get('name') for s in steps for c in s.get('tool_calls',[]))
        tool_errors = [dict(step=s['step'],name=o.get('name'),content=o.get('content'))
                       for s in steps for o in s.get('observation',[]) if o.get('error')]
        cases.append(dict(task_id=task,profile=profile,tool_counts=dict(tool_counts),
                          errors=errors,tool_errors=tool_errors,
                          parallel_read_batches=sum(any(o.get('parallel_batch_size',1)>1 for o in s.get('observation',[])) for s in steps),
                          summary_counters=summary.get('counters',{})))
    csv_write(DOCS / 'results.csv', rows)
    matrix = []
    for task in plan['tasks']:
        pair = {r['profile']:r for r in rows if r['task_id'] == task}
        matrix.append(dict(task_id=task, v1_resolved=pair['v1']['resolved'], v3_resolved=pair['v3']['resolved'],
                           v1_judge_status=pair['v1']['judge_status'],v3_judge_status=pair['v3']['judge_status'],
                           v1_evidence_path=pair['v1']['evidence_path'],v3_evidence_path=pair['v3']['evidence_path']))
    csv_write(DOCS / 'task_matrix.csv', matrix)
    holdout_ids = (ROOT / 'holdout.txt').read_text().split()
    key = load_key().encode()
    findings, control_metadata_findings, scanned = [], [], 0
    scan_paths = [p for p in OUT.rglob('*') if p.is_file()]
    scan_paths += [p for p in (ROOT / 'logs/run_evaluation').glob('interview-real-20261009-*/*/**/*') if p.is_file()]
    for path in scan_paths:
        data = path.read_bytes()
        parts = [data]
        if path.name.startswith('workspace-') and path.suffix == '.json':
            snap = json.loads(data)
            if snap.get('kind') == 'docker':
                parts += [base64.b64decode(snap['patch']),base64.b64decode(snap['untracked_tar'])]
        if key and any(key in part for part in parts):
            findings.append(dict(path=str(path.relative_to(ROOT)),kind='actual_api_key'))
        if any(h.encode() in part for h in holdout_ids for part in parts):
            if path == OUT / 'plan.json':
                permitted = ('protected_source','protected_eval','source_status')
                non_control = json.dumps({k:v for k,v in plan.items() if k not in permitted}).encode()
                assert not any(h.encode() in non_control for h in holdout_ids)
                control_metadata_findings.append(dict(path=str(path.relative_to(ROOT)),
                    kind='historical_artifact_filenames_only',
                    reason='Hash inventory/status used to prove preservation; not model context or task content'))
            else:
                findings.append(dict(path=str(path.relative_to(ROOT)),kind='holdout_id'))
        if any(re.search(rb'(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9_]{30,})', part) for part in parts):
            findings.append(dict(path=str(path.relative_to(ROOT)),kind='credential_pattern_requires_review'))
        scanned += 1
    commands = [dict(label=p.parent.name,**read_json(p)) for p in sorted((OUT/'commands').glob('*/command.json'))]
    provenance = [p for p in (ROOT / '.cache/public-image-transfer').glob('*/*.json') if p.is_file()]
    for p in [*scan_paths, *provenance]:
        if p.is_file():
            evidence.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p),size_bytes=p.stat().st_size))
    evidence = list({row['path']:row for row in evidence}.values())
    audit = dict(scanned_files=scanned, findings=findings, passed=not findings,
                 control_metadata_findings=control_metadata_findings,
                 source_protected_unchanged=protected(SOURCE)==plan['protected_source'],
                 eval_protected_unchanged=protected(ROOT)==plan['protected_eval'],
                 source_head_unchanged=capture(['git','rev-parse','HEAD'],SOURCE)==plan['source_sha'],
                 source_status_unchanged=capture(['git','status','--porcelain'],SOURCE)==plan['source_status'],
                 main_head=capture(['git','rev-parse','main'],SOURCE),
                 v3_head=capture(['git','rev-parse','v3'],SOURCE),
                 frozen_business_code_unchanged=not capture(['git','diff',plan['base_sha'],'--','src','tests','scripts/run_agent.py']))
    manifest = dict(created=now(),base_git_sha=plan['base_sha'],
                    actual_run_git_shas=sorted({r['git_sha'] for r in rows if r['git_sha']}),
                    report_generator_git_sha=capture(['git','rev-parse','HEAD']),
                    tasks=plan['tasks'],planned_runs=len(rows),actual_provider_runs=sum(r['real_provider'] for r in rows),
                    judged_runs=sum(r['judge_status']=='JUDGED' for r in rows),
                    config_hashes={r['profile']:r['config_sha256'] for r in rows},
                    evidence=evidence,audit=audit,
                    caveats=['Known DEV exploratory regression comparison; not held-out generalization.',
                             'Cost is frozen project-price estimate, not a provider bill.',
                             'Cache tokens are a subset of prompt tokens; do not add them twice.',
                             'Per-task Judge wall time unavailable; see per-profile command wall time.'])
    write_json(DOCS/'evidence_manifest.json',manifest)
    write_json(DOCS/'derived_data.json',dict(rows=rows,matrix=matrix,cases=cases,commands=commands,audit=audit))
    print(json.dumps(dict(rows=len(rows),judged=manifest['judged_runs'],audit=audit),ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
