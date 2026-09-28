# RepoFix 1.1 Results

- Date: 2026-09-28
- Python: 3.12.3
- Dataset / split: `SWE-bench/SWE-bench_Verified` / `test`
- RANDOM_SEED = 20260928
- Git branch: `main`
- Git remote: `git@github.com:azbycxd/repofix.git`
- Docker: Client 29.4.1 / Server 29.4.1

- DEV_TASK_1 = django__django-16429
- DEV_TASK_2 = django__django-15277
- DEV_TASK_3 = django__django-13343
- DEV_TASK_4 = django__django-16454
- DEV_TASK_5 = django__django-16950
- HOLDOUT_TASK_1 = django__django-11099
- HOLDOUT_TASK_2 = django__django-12713
- HOLDOUT_TASK_3 = django__django-11477

- GOLD_DEV_1 = RESOLVED
- GOLD_DEV_2 = RESOLVED
- GOLD_DEV_3 = RESOLVED
- GOLD_DEV_4 = RESOLVED
- GOLD_DEV_5 = RESOLVED
- GOLD_HOLDOUT_1 = RESOLVED
- GOLD_HOLDOUT_2 = RESOLVED
- GOLD_HOLDOUT_3 = RESOLVED
- GOLD_RESOLVED = 8 / 8

- Gold harness elapsed: 284.46 seconds
- Docker disk usage: Images 23 / 27.76GB (19.65GB reclaimable); Containers 26 / 42MB (41.84MB reclaimable); Local Volumes 14 / 1.048GB (823MB reclaimable); Build Cache 0B

- HOLDOUT_STATUS = SEALED
- HOLDOUT_AGENT_RUNS = 0

## Step 1.2 experiments

| Date | Commit / config | Model | Task | Resolved | Steps | Tokens | Cost | Notes |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |
| 2026-09-29 | `66bd442` / thinking disabled, max 50 steps, $0.5 cap | `deepseek-flash` | `django__django-16429` | YES | 8 | prompt 24226 / completion 1128 / cache 20224 | $0.002675544 max estimate | provider 8; tools 9; wall 11.99s; patch yes; terminal submitted; issue 自带文件和修法；修后验证，未先复现 |

Deferred live-review item (not changed in this round): filtering reproduction
scripts from `get_diff`.

## Step 1.3 DEV baseline

| Task | Resolved | Steps | Cost |
| --- | --- | ---: | ---: |
| `django__django-16429` | YES | 5 | $0.001322988 |
| `django__django-15277` | YES | 7 | $0.002455224 |
| `django__django-13343` | YES | 24 | $0.012987696 |
| `django__django-16454` | YES | 36 | $0.010402284 |
| `django__django-16950` | YES | 50 | $0.042569856 |

- Date: 2026-09-29
- Run ID: `step-1-3-minimal-baseline`
- Git commit: `4318e33cad71d4f8a319355b85bf02e1f9b092f1`
- Model / config: `deepseek-flash`; thinking disabled; 50-step limit; $0.5 cost limit; SDK `max_retries=2`
- Resolved tasks: `django__django-16429`, `django__django-15277`, `django__django-13343`, `django__django-16454`, `django__django-16950`
- Resolved count for this five-task DEV baseline: 5 / 5
- Total Provider calls / tool calls: 122 / 140
- Total tokens: prompt 1,653,020 / cache hit 1,601,408 / completion 37,205 / prompt + completion 1,690,225
- Total cost: $0.069738048 maximum estimate
- Agent total wall time: 328.45 seconds
- Harness wall time: 136.25 seconds
