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

Deferred live-review items (not changed in this round): SDK `max_retries=0`,
adding the git commit to the first trajectory record, and filtering reproduction
scripts from `get_diff`.
