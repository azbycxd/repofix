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

## Stage 2 temperature=0 baseline

| Task | Resolved | Steps | Prompt Tokens | Cost |
| --- | --- | ---: | ---: | ---: |
| `django__django-16429` | YES | 9 | 26,737 | $0.002790444 |
| `django__django-15277` | YES | 7 | 16,851 | $0.002022420 |
| `django__django-13343` | YES | 28 | 195,010 | $0.009346200 |
| `django__django-16454` | NO (`missing_module`, ambiguous) | 27 | 218,507 | $0.009944484 |
| `django__django-16950` | YES | 30 | 240,924 | $0.011315904 |

- Date: 2026-09-29
- Run ID: `temperature-0-baseline`
- Code commit: `1d47a16d836cfa3017c42cb171ccbdc059d7af5b`
- Model / config: `deepseek-flash`; temperature 0; thinking disabled; 50-step limit; $0.5 cost limit; output threshold 12,000 characters
- Resolved count: 4 / 5
- Total steps: 101
- Total prompt / cache-hit / completion tokens: 698,029 / 660,992 / 16,952
- Total cost: $0.035419452 maximum estimate
- Agent wall time: 267.40 seconds
- Harness wall time: 33.78 seconds
- `django__django-16950`: 不稳定题：历史两次运行一次成功、一次失败。本次 temperature=0 baseline 为 RESOLVED。

## Step 2.2 view + str_replace comparison

| Task | Baseline Result | 2.2 Result | Baseline Steps | 2.2 Steps | Baseline Cost | 2.2 Cost | View Calls | StrReplace Calls | StrReplace Failures | Rollbacks |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `django__django-16429` | RESOLVED | RESOLVED | 9 | 6 | $0.002790444 | $0.001522716 | 0 | 1 | 0 | 0 |
| `django__django-15277` | RESOLVED | RESOLVED | 7 | 15 | $0.002022420 | $0.003938292 | 1 | 1 | 0 | 0 |
| `django__django-13343` | RESOLVED | RESOLVED | 28 | 23 | $0.009346200 | $0.008201340 | 3 | 5 | 0 | 0 |
| `django__django-16454` | UNRESOLVED (`missing_module`, ambiguous) | RESOLVED | 27 | 18 | $0.009944484 | $0.005915856 | 0 | 3 | 0 | 0 |
| `django__django-16950` | RESOLVED | RESOLVED | 30 | 25 | $0.011315904 | $0.010855116 | 4 | 3 | 0 | 0 |

| Task | Baseline Prompt | 2.2 Prompt | Baseline Completion | 2.2 Completion |
| --- | ---: | ---: | ---: | ---: |
| `django__django-16429` | 26,737 | 14,161 | 1,380 | 457 |
| `django__django-15277` | 16,851 | 51,763 | 1,079 | 1,913 |
| `django__django-13343` | 195,010 | 170,005 | 4,508 | 3,690 |
| `django__django-16454` | 218,507 | 95,452 | 4,244 | 2,768 |
| `django__django-16950` | 240,924 | 228,097 | 5,741 | 4,926 |

- Date: 2026-09-29
- Run ID: `step-2-2-view-str-replace`
- Code commit: `7cc1a68cfc2b34b587ac79f882669df60ee1dfca`
- Model / config: `deepseek-flash`; temperature 0; thinking disabled; 50-step limit; $0.5 cost limit; output threshold 12,000 characters; native tools `bash`, `view`, `str_replace`, `submit`
- Baseline resolved: 4 / 5; Step 2.2 resolved: 5 / 5
- Total steps: 101 -> 87 (-14)
- Total prompt tokens: 698,029 -> 559,478 (-138,551)
- Total cache-hit tokens: 660,992 -> 523,520 (-137,472)
- Total completion tokens: 16,952 -> 13,754 (-3,198)
- Total cost: $0.035419452 -> $0.030433320 (-$0.004986132), maximum estimates
- Agent wall time: 267.40 -> 334.85 seconds
- Harness wall time: 33.78 -> 34.11 seconds
- Tool calls in Step 2.2: bash 74; view 8; str_replace 13; submit 5
- Of the 74 bash calls, 7 invoked `sed` and 43 invoked `python`; the model adopted the native tools but still relied heavily on bash and Python scripts.
- StrReplace failures: 0; syntax rollbacks: 0
- `django__django-16950`: RESOLVED in 25 steps; it did not reach the 50-step limit in either temperature=0 run.
- HOLDOUT_STATUS = SEALED
- HOLDOUT_AGENT_RUNS = 0

## Step 2.3 BM25 code search

The index uses the existing Python AST chunks and 50-line fallback chunks. A
standard-library BM25 implementation tokenizes prose, paths, snake_case, and
CamelCase; file path and symbol tokens receive double weight. Only UTF-8 tracked
source files from the current `/testbed` Git revision are indexed.

### Offline DEV file localization

| Task | Gold File | Rank | Top1 | Top3 | Top5 | Files | Chunks | Build |
| --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: |
| `django__django-16429` | `django/utils/timesince.py` | 2 | NO | YES | YES | 3,282 | 34,482 | 7.16s |
| `django__django-15277` | `django/db/models/fields/__init__.py` | 2 | NO | YES | YES | 3,237 | 33,376 | 7.20s |
| `django__django-13343` | `django/db/models/fields/files.py` | 1 | YES | YES | YES | 3,132 | 31,435 | 9.40s |
| `django__django-16454` | `django/core/management/base.py` | 1 | YES | YES | YES | 3,263 | 34,394 | 11.73s |
| `django__django-16950` | `django/forms/models.py` | 4 | NO | NO | YES | 3,284 | 34,941 | 11.63s |

- Recall@1: 2 / 5 (0.40)
- Recall@3: 4 / 5 (0.80)
- Recall@5: 5 / 5 (1.00)
- Total offline index build time: 47.12 seconds
- Gold patches were read only by the offline evaluator to extract file labels;
  no gold data entered the Agent context.

### Step 2.2 vs Step 2.3 Agent comparison

| Task | 2.2 Result | 2.3 Result | 2.2 Steps | 2.3 Steps | 2.2 Cost | 2.3 Cost | Search Calls | First Correct File Step |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `django__django-16429` | RESOLVED | RESOLVED | 6 | 10 | $0.001522716 | $0.002749404 | 0 | 1 |
| `django__django-15277` | RESOLVED | RESOLVED | 15 | 7 | $0.003938292 | $0.002014428 | 0 | 1 |
| `django__django-13343` | RESOLVED | RESOLVED | 23 | 17 | $0.008201340 | $0.005096664 | 0 | 1 |
| `django__django-16454` | RESOLVED | UNRESOLVED (`missing_module`, ambiguous) | 18 | 21 | $0.005915856 | $0.007559736 | 0 | 1 |
| `django__django-16950` | RESOLVED | RESOLVED | 25 | 40 | $0.010855116 | $0.023469936 | 1 | 4 |

- Date: 2026-09-29
- Run ID: `step-2-3-bm25-search`
- Code commit: `6e84b0e3b57702bf0b25cec4356327972d95afc1`
- Model / config: `deepseek-flash`; temperature 0; thinking disabled;
  50-step limit; $0.5 cost limit; output threshold 12,000 characters;
  native tools `bash`, `view`, `str_replace`, `search_code`, `submit`
- Resolved count: Step 2.2 = 5 / 5; Step 2.3 = 4 / 5
- Total steps: 87 -> 95 (+8)
- Total prompt tokens: 559,478 -> 1,008,274 (+448,796)
- Total cache-hit tokens: 523,520 -> 963,328 (+439,808)
- Total completion tokens: 13,754 -> 18,022 (+4,268)
- Total cost: $0.030433320 -> $0.040890168 (+$0.010456848), maximum estimates
- Step 2.3 Provider calls / tool calls: 95 / 105
- Step 2.3 Agent wall time: 439.38 seconds
- Step 2.3 live index build time: 37.62 seconds total
- Step 2.3 Harness wall time: 39.38 seconds
- Search calls: 1 total, only on `django__django-16950`. The Agent followed
  the top search result into `django/contrib/admin/options.py`, then found the
  correct `django/forms/models.py` file with a manual grep at step 4. The gold
  file was not visible in the truncated search observation, so this is evidence
  that search changed the early path but not that it directly located the fix.
- The other four tasks did not call `search_code`; their metric differences
  cannot be attributed to BM25. The 16454 ambiguous failure occurred without a
  search call and matches a failure class already seen in the temperature=0
  baseline, so model/harness variation remains a plausible cause.
- `django__django-16454` submitted a nonempty patch after targeted and broader
  tests. It modified management parser behavior and added tests, but the official
  gold-test patch could not apply cleanly and its expected
  `subparser_vanilla.py` module was absent; Harness classified the failure as
  `missing_module` and ambiguous.
- BM25 Full-Issue Recall@5 = 5 / 5, but the Agent actually called
  `search_code` only once. Most of these five DEV issues expose enough file or
  function information for direct grep-based localization. On 16950 the old
  source-bearing search response exceeded the 12,000-character observation
  limit and was truncated; Step 2.4 changes search output to locations only.
  The 16454 result difference came from extra untracked test files entering the
  prediction patch, not from BM25. Step 2.3 is therefore qualitative evidence
  that retrieval works, but not evidence that it improves Agent success.
- HOLDOUT_STATUS = SEALED
- HOLDOUT_AGENT_RUNS = 0

## Step 2.4 full-dense indexing cost limit

- Date: 2026-09-29
- Code commits: `707bff9` (Dense + RRF), `2d6307a` (chunk-level cache)
- Embedding: `fastembed==0.8.1`; `BAAI/bge-small-en-v1.5`; 384 dimensions;
  path/symbol/type plus the first 512 source characters per chunk
- BM25 and RRF remained unchanged (`RRF_K=60`, rank window 60). No Recall-based
  tuning was performed.
- `STEP_2_4_FULL_DENSE = ABORTED_FOR_ENGINEERING_COST`

### Observed indexing cost and cache reuse

| Measurement | Value |
| --- | ---: |
| DEV1 chunks / unique embedding inputs | 34,482 / 34,482 |
| DEV1 first-cold embeddings | 34,482 |
| DEV1 observed cold boundary | about 36m25s |
| DEV1 same-revision cache hits / new embeddings | 34,482 / 0 |
| DEV1 same-revision cache load | 61.747s |
| DEV2 chunks / unique embedding inputs | 33,376 / 33,376 |
| DEV2 inputs reused directly from DEV1 | 10,012 |
| DEV2 new inputs required | 23,364 |
| DEV2 new embeddings completed before abort | 4,864 |
| DEV2 new embeddings remaining at abort | 18,500 |
| Cache unique embeddings at abort | 39,346 |
| Cache file size at abort | 184,487,936 bytes (175.94 MiB) |
| Current chunk-cache run, cache-active interval | about 42m56s |

The DEV1 cold boundary is reconstructed from creation of the empty chunk-cache
database at 12:16:37 to creation of the DEV2 container at 12:53:02. It is an
upper bound that also includes the final DEV1 ranking and container transition.
The cache-active interval ends at the final committed cache write at 12:59:33.
Earlier full-matrix cold attempts consumed more than another 63 minutes,
so the full-Dense implementation investigation consumed more than 1h45m in
cold CPU work before it was stopped. The SQLite cache passed `integrity_check`.

### Chunking redundancy diagnosis

| Task | Functions | Classes | Methods | Line Blocks | Total | Mean Chars | P50 | P95 | Max | Full-source Duplicate | Embedding-input Duplicate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `django__django-16429` | 1,641 | 6,876 | 24,772 | 1,193 | 34,482 | 878.247 | 355 | 2,585 | 224,016 | 43.83% | 8.57% |
| `django__django-15277` | 1,613 | 6,748 | 23,854 | 1,161 | 33,376 | 834.921 | 339 | 2,473 | 185,862 | 43.55% | 8.76% |
| `django__django-13343` | 1,528 | 6,490 | 22,310 | 1,107 | 31,435 | 829.952 | 337 | 2,471.6 | 171,889 | 43.30% | 8.99% |
| `django__django-16454` | 1,628 | 6,865 | 24,719 | 1,182 | 34,394 | 876.227 | 354 | 2,580 | 224,016 | 43.84% | 8.56% |
| `django__django-16950` | 1,657 | 6,928 | 25,131 | 1,225 | 34,941 | 882.474 | 356 | 2,583 | 241,838 | 43.72% | 8.50% |

Every direct class-method chunk was found verbatim inside its corresponding
top-level-class chunk. Across the five revisions, full-source duplication
averaged 43.65%; after the fixed 512-character representation and metadata are
accounted for, duplicated method source was about 8.68% of embedding input.
This is recorded as a later candidate optimization; chunk definitions were not
changed during Step 2.4.

- `CHUNKING_REDUNDANCY_DIAGNOSIS = CLASS_AND_METHOD_OVERLAP_CONFIRMED`
- `DENSE_COLD_BUILD_COST = TOO_HIGH_FOR_CURRENT_CPU_IMPLEMENTATION`
- `CACHE_REUSE_EFFECT = 10,012 DEV2 inputs reused; 23,364 still required new embeddings`
- The five-DEV offline evaluation was stopped during DEV2. No complete Dense or
  RRF Recall metrics are reported, and DEV3 through DEV5 were not built.
- Provider calls: 0; Agent runs: 0
- HOLDOUT_STATUS = SEALED
- HOLDOUT_AGENT_RUNS = 0

当前 full-repository chunk-level Dense embedding 在 CPU 环境下冷构建成本过高，
因此停止该实现路线。该结果只否定当前工程实现方式，不证明 Dense retrieval 的
检索质量无效。

## Step 2.8 BM25 control

This control uses the repaired, position-only BM25 search path before adding
the reproduction-first prompt or telemetry. Dense/RRF is disabled.

| Task | Harness | Terminal | Steps | Cost | Pre-fix Reproduced | Repro Flipped | First Production Edit | Existing Test Modified | Git-History Searches | Network Attempts |
| --- | --- | --- | ---: | ---: | --- | --- | ---: | --- | ---: | ---: |
| `django__django-16429` | RESOLVED | submitted | 10 | $0.002726268 | NO | NO | 2 | YES | 1 | 0 |
| `django__django-15277` | RESOLVED | submitted | 8 | $0.002635248 | NO | NO | 3 | NO | 1 | 0 |
| `django__django-13343` | RESOLVED | submitted | 19 | $0.005581212 | NO | NO | 4 | YES | 1 | 0 |
| `django__django-16454` | RESOLVED | submitted | 19 | $0.005276484 | NO | NO | 2 | YES | 1 | 0 |
| `django__django-16950` | UNRESOLVED | max_steps | 50 | $0.031352208 | YES | NO | 17 | YES | 5 | 1 |

- Date: 2026-09-29
- Run ID: `step-2-8-bm25-control`
- Code commit: `91668d006d83700a6ddfe1b93a5fee68378380ab`
- Model / config: `deepseek-flash`; temperature 0; thinking disabled;
  50-step limit; $0.5 cost limit; default BM25; original pre-2.8 prompt
- Harness result: 4 / 5; unresolved: `django__django-16950`
- Total steps: 106; prompt/cache-hit/completion tokens:
  1,163,609 / 1,118,720 / 22,827
- Total cost: $0.047571420 maximum estimate
- Agent wall time: 253.81 seconds; Harness wall time: about 111 seconds
- Git-history searches: 9 shell tool calls; network attempts: 1
- Pre-fix reproduction: 1 / 5; same-criterion failure-to-pass flips: 0 / 5
- Existing test files appeared in 4 / 5 final patches. For 16429, 13343, and
  16454 this accompanied a production fix and added regression coverage. The
  16950 patch modified only an existing test file after extensive debugging;
  it never produced a valid production fix and exhausted 50 steps.
- Trajectory secret/HOLDOUT audit: PASS
- HOLDOUT_STATUS = SEALED
- HOLDOUT_AGENT_RUNS = 0

## Step 2.8 reproduction-first comparison

This run adds only the lightweight reproduction-first instructions and behavior
telemetry to the corrected BM25 control. It does not add a planner, stage
machine, evidence gate, context strategy, or reviewer.

| Task | Control Result | 2.8 Result | Control Steps | 2.8 Steps | Pre-fix Repro | Repro Flipped | Existing Test Modified | Git-History Searches | Network Attempts | Cost |
| --- | --- | --- | ---: | ---: | --- | --- | --- | ---: | ---: | ---: |
| `django__django-16429` | RESOLVED | RESOLVED | 10 | 10 | YES | YES | NO | 1 -> 1 | 0 -> 0 | $0.002726268 -> $0.003056796 |
| `django__django-15277` | RESOLVED | RESOLVED | 8 | 14 | NO | NO | NO | 1 -> 1 | 0 -> 0 | $0.002635248 -> $0.004026876 |
| `django__django-13343` | RESOLVED | RESOLVED | 19 | 30 | YES | YES | YES | 1 -> 1 | 0 -> 0 | $0.005581212 -> $0.010154004 |
| `django__django-16454` | RESOLVED | UNRESOLVED (ambiguous: missing module) | 19 | 41 | YES | YES | YES | 1 -> 1 | 0 -> 0 | $0.005276484 -> $0.013859304 |
| `django__django-16950` | UNRESOLVED | RESOLVED | 50 | 32 | YES | YES | YES | 5 -> 3 | 1 -> 0 | $0.031352208 -> $0.013380744 |

- Date: 2026-09-29
- Run ID: `step-2-8-reproduction-first`
- Code commit: `491e0bd3438c211757146b621927180c81ccfc82`
- Model / config: `deepseek-flash`; temperature 0; thinking disabled;
  50-step limit; $0.5 cost limit; default BM25
- Harness result: 4 / 5. Resolved: `django__django-16429`,
  `django__django-15277`, `django__django-13343`, and
  `django__django-16950`.
- Total steps: 106 -> 127 (+21). Prompt/cache-hit/completion tokens:
  1,163,609 / 1,118,720 / 22,827 ->
  1,068,213 / 1,019,904 / 19,888.
- Total cost: $0.047571420 -> $0.044477724 (-$0.003093696), maximum
  estimates. Agent wall time: 253.81 -> 591.16 seconds. Harness wall time:
  about 108 seconds.
- Automatic pre-fix reproduction telemetry: 1 / 5 -> 4 / 5.
  Same-criterion automatic failure-to-pass flips: 0 / 5 -> 4 / 5.
- 人工轨迹核查：5/5 在生产修改前真实复现；自动 telemetry 仅作辅助统计。
  From Step 2.9 onward, any `ModuleNotFoundError` or `ImportError` is treated
  as an environment/reproduction-script error and cannot set
  `PRE_FIX_REPRODUCED`.
- First production edit steps in task order: 3, 4, 8, 19, 18.
- Git-history searches: 9 -> 7. Network attempts: 1 -> 0. In particular,
  16950 changed from five history searches plus one download attempt to three
  history searches and no network attempt, then submitted at step 32 instead
  of exhausting all 50 steps.
- Existing test files appeared in 4 / 5 control patches and 3 / 5 2.8
  patches. The 2.8 edits add regression coverage for 13343 and 16454; the
  16950 assertion is changed alongside the production fix to express the new
  required behavior. No 2.8 patch merely weakens an assertion to hide a
  failing implementation.
- 16454 is recorded as official UNRESOLVED. Its intended fail-to-pass test and
  the Agent-added regression test passed, but editing the same existing test
  file made the Harness gold-test patch fail to apply. The missing injected
  `subparser_vanilla.py` then produced the reported `missing_module` ambiguous
  failure. The task was not rerun.
- Step 2.9 patch-policy rejudge, using the exact same 2.8 Agent patch and no
  Agent rerun: original full-patch Harness result = UNRESOLVED/ambiguous;
  production-only filtered-patch result = RESOLVED. The filtered path was
  `tests/user_commands/tests.py`; Harness wall time was 35.81 seconds.
- Agent full diffs remain in trajectory/run artifacts. Official Harness
  predictions exclude paths recognized by `is_test_path()`: official scoring
  evaluates production changes only, while Agent-authored tests remain
  separately reviewable in the trajectory.
- Aggregate Harness count did not regress (4 / 5 -> 4 / 5), but the unresolved
  task changed from 16950 to the ambiguous 16454 result. Behavior improved on
  reproduction, history searching, network use, and the prior 16950 failure;
  steps and wall time increased.
- Offline tests: 13 passed; fixed five-DEV Docker/offline check passed.
- Trajectory secret/HOLDOUT audit: PASS
- HOLDOUT_STATUS = SEALED
- HOLDOUT_AGENT_RUNS = 0

## Step 2.9 single-pass independent Reviewer

The corrected Step 2.8 comparison uses production-only evaluation patches;
16454 is RESOLVED by the recorded filtered-patch rejudge. Step 2.9 keeps
reproduction-first, default BM25, temperature 0, thinking disabled, and the
same Agent tools and limits. Reviewer is explicitly enabled for this experiment.

| Task | 2.8 Result | 2.9 Result | Reviewer | Reject Reason | Patch Changed After Reject | Final Harness |
| --- | --- | --- | --- | --- | --- | --- |
| `django__django-16429` | RESOLVED | RESOLVED | APPROVE | — | N/A | RESOLVED |
| `django__django-15277` | RESOLVED | RESOLVED | APPROVE | — | N/A | RESOLVED |
| `django__django-13343` | RESOLVED | RESOLVED | APPROVE | — | N/A | RESOLVED |
| `django__django-16454` | RESOLVED (filtered rejudge) | RESOLVED | APPROVE | — | N/A | RESOLVED |
| `django__django-16950` | RESOLVED | UNRESOLVED | APPROVE | — | N/A | UNRESOLVED |

- Date: 2026-09-29
- Run ID: `step-2-9-independent-reviewer`
- Code commit: `5cd6d92ff9a99b64075370188103309e9f7d3b79`
- Reviewer context: a new single-turn context containing only the public issue,
  complete Agent diff, last usable reproduction/test command, and its output.
  It receives no Agent history, tools, gold patch, official fix, or HOLDOUT data.
- Reviewer protocol: exactly `APPROVE` or `REJECT: <one-sentence reason>`.
- Reviewer verdicts: 5 APPROVE / 0 REJECT / 0 errors. No patch was returned to
  the Agent, so improvements after REJECT = 0 and false rejections = 0.
- Reviewer usage: prompt/cache-hit/completion tokens = 6,801 / 0 / 15;
  total tokens = 6,816; cost = $0.002058300 maximum estimate; measured added
  latency = 4.498 seconds across five calls.
- Agent steps: 127 -> 126. Agent-only cost: $0.044477724 -> $0.042787680.
  Step 2.9 Agent + Reviewer cost = $0.044845980 maximum estimate. Agent wall
  time = 436.25 seconds; Harness wall time = 116.70 seconds.
- Corrected production-only Harness comparison: 5 / 5 -> 4 / 5. The Reviewer
  never changed an Agent patch, so the result difference is not an effect of a
  Reviewer return; it is run-to-run Agent behavior, with 16950 producing a new
  incorrect implementation.
- 16950 is a false approval. Reviewer saw a broad command reporting 1,708 tests
  passed, but that command omitted `model_formsets.test_uuid`. The submitted
  `AutoField` condition broke five existing UUID formset behaviors in the
  official Harness. Reviewer did not detect the insufficient validation or
  incorrect condition.
- Reviewer did not catch assertion weakening or another real defect on this
  DEV set. No 2.9 patch merely weakened an existing assertion, but test-file
  changes in 13343, 16454, and 16950 were all approved without comment.
- Conclusion: current DEV set shows no clear Reviewer benefit and one important
  false approval. Reviewer remains implemented but disabled by default.
- Trajectory secret/HOLDOUT audit: PASS; exactly one Reviewer event per task.
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

- `django__django-16950`: 已修好；未 submit，50 steps 耗尽。

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

## Step 2.1 tool-output truncation comparison

| Task | 1.3 Result | 2.1 Result | 1.3 Steps | 2.1 Steps | 1.3 Prompt Tokens | 2.1 Prompt Tokens | 1.3 Cost | 2.1 Cost | Truncations |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `django__django-16429` | RESOLVED | RESOLVED | 5 | 9 | 10,725 | 28,080 | $0.001322988 | $0.002641584 | 0 |
| `django__django-15277` | RESOLVED | RESOLVED | 7 | 9 | 18,054 | 20,395 | $0.002455224 | $0.002038356 | 0 |
| `django__django-13343` | RESOLVED | RESOLVED | 24 | 24 | 267,324 | 108,327 | $0.012987696 | $0.006316692 | 0 |
| `django__django-16454` | RESOLVED | RESOLVED | 36 | 19 | 250,077 | 96,716 | $0.010402284 | $0.006277008 | 0 |
| `django__django-16950` | RESOLVED | UNRESOLVED | 50 | 50 | 1,106,840 | 663,281 | $0.042569856 | $0.027805500 | 0 |

- Date: 2026-09-29
- Run ID: `step-2-1-output-truncation`
- Git commit: `d7999723d1f97727070ddfc8c82fd553cdae85aa`
- Model / config: `deepseek-flash`; thinking disabled; 50-step limit; $0.5 cost limit; SDK `max_retries=2`; output threshold 12,000 characters (head 6,000 + tail 6,000)
- Resolved tasks: `django__django-16429`, `django__django-15277`, `django__django-13343`, `django__django-16454`
- Resolved count for this five-task DEV comparison: 1.3 = 5 / 5; 2.1 = 4 / 5
- Total steps: 122 -> 111 (-11)
- Total prompt tokens: 1,653,020 -> 916,799 (-736,221)
- Total cache-hit tokens: 1,601,408 -> 880,640 (-720,768)
- Total completion tokens: 37,205 -> 24,123 (-13,082)
- Total cost: $0.069738048 -> $0.045079140 (-$0.024658908), maximum estimates
- Agent total wall time: 328.45 -> 363.69 seconds
- Harness wall time: 136.25 -> 37.98 seconds
- Actual truncations: 0; affected tasks: none
- No tool output crossed the configured threshold, so the observed run-to-run metric changes cannot be attributed to truncation. The threshold was not adjusted and no task was rerun.
- HOLDOUT_STATUS = SEALED
- HOLDOUT_AGENT_RUNS = 0

## Step 3.1 local Python repository CLI demo

- Date: 2026-09-29
- Repository: Colorama `0.4.6`
- Upstream commit: `3de9f013df4b470069d03d250224062e8cf15c49`
- Planted-bug commit: `5dedf963021fe539f1194db91de1b44779b8ae0d`
- Agent runs: 1; terminal: submitted; steps/provider calls/tool calls: 42/42/53
- Tokens: prompt 562,262 / cache hit 544,896 / completion 8,026
- Estimated cost: $0.018110376; total CLI wall time: 117.587 seconds
- PRE_FIX_REPRODUCED = true; REPRO_FLIPPED = true
- Final change: `colorama/ansi.py`; full diff equals production-only diff
- Agent tests: 38 passed / 14 skipped; independent verification: focused
  reproduction passed and 52 unittest cases ran with 14 platform skips
- Source repository HEAD/status unchanged; temporary worktree/container/image
  cleaned; runtime Docker network mode was `none`
- Default retrieval: BM25 (1 search); Dense loaded: false; Reviewer calls: 0
- Trajectory secret/HOLDOUT audit: PASS
- HOLDOUT_STATUS = SEALED
- HOLDOUT_AGENT_RUNS = 0
