# RepoFix V3 progress

## Post-review follow-up (2026-10-09)

- Added a narrowly scoped temporary_patch_file context manager for apply/restore
  JSON payloads. Docker archive entries are root-owned 0644 with sandbox_user
  (0600 otherwise); cleanup always runs as root in finally, including interrupted
  helpers. Sandbox code only reads the payload and no longer attempts unlink in
  sticky-bit /tmp. Cleanup accepts only generated apply/restore UUID paths.
  Ordinary tool-output permissions remain unchanged. Archive-metadata and root
  cleanup tests cover both payload types and interruption; local real-helper
  edit/rollback/restore tests also pass. Full pytest: 132 passed, 2 Docker skips;
  unchanged V1 golden PASS. Real non-root Docker integration remains unverified.

- Restored the pre-dispatch checkpoint immediately after assistant messages and
  pending_calls are recorded. F10 had incorrectly removed this recovery boundary;
  only readonly-tool post-checkpoints remain omitted. Step-end and mutating-tool
  checkpoints remain. A single bash interrupted by KeyboardInterrupt now resumes
  with an interrupted tool observation, the pre-tool workspace and preserved
  usage; no automatic tool replay. Updated checkpoint-count regression test.
  Full offline pytest and unchanged V1 golden: PASS (120 passed, 2 Docker skips).

Date: 2026-10-09 (Asia/Shanghai). Branch: `v3`. Base: `3fe3c0e`.
Scope: M0–M13 followed by REPOFIX_V3_FIX.md F1–F12, offline only.
First round was subsequently pushed at the user's explicit request; this repair
round creates local commits only, with no push or merge.

## Second-round fixes (2026-10-09)

- F12: Corrected the historical deviation claims and DESIGN sections. Masked
  outputs now have redacted container-readable /tmp copies, rehydrated on resume;
  actual local shell can read them. Kept DockerEnv's direct-child /tmp contract
  instead of adding a new directory API. Final cross-check also corrects runner
  setup failures to runtime_error rather than interrupted. Full pytest: 119 passed, 2 skipped,
  5 subtests passed. Remaining integration limitations are listed below.

- F11: Shared frozen definitions moved to core.py (agent.py re-exports public
  names). V1Loop now takes an explicit agent dependency and uses the original
  ordinary-local run body from 3fe3c0e; no state bag or monkey-patched dispatch.
  Shell/file/search/submit/child handlers live in tools/, Runtime assembles them.
  Function-local imports were lifted; optional Dense alone uses an explicit
  import_module only when selected, preserving a dependency-light default.
  Ruff 0.16.10 formats src/scripts/tests at width 100; all src lines <=120,
  enforced by a structural test. Full pytest and V1 golden PASS.

- F10: Step traces include only new events. Checkpoints occur at step end and
  after bash/str_replace/apply_patch/verify, not readonly tools. Polling backs
  off 0.1/0.2/0.5/1 seconds and seeks log tails; completed foreground output is
  fetched once in full. Children reuse the parent BM25 snapshot. Counter,
  event, index-build and polling tests plus full pytest PASS.

- F9: Used the explicitly allowed real-helper alternative. Extended the
  disposable local Git backend to execute real shell commands. Tests now cover
  grep fallback with rg absent, background timeout/poll/kill/environment,
  310 KB Runtime str_replace/apply_patch and external-write read guards.
  The 11 hasattr(env, "files") branches remain for deterministic fake scripts
  and golden fixtures (workspace 5, shell 4, checkpoint 1, grep 1), plus the
  separate fake snapshot restoration format; they are NOT Docker evidence. Replacing
  the entire fixture would change test semantics; production helper paths are
  exercised explicitly instead. Real container lifecycle remains opt-in.

- F8: Quote-aware parsing consumes redirections without inventing commands;
  nested $() and backtick commands are separately checked, including redirect
  targets. Literal single-quoted substitutions remain literal. Pytest PASS.
  This remains a lexical policy, not a complete shell parser/security boundary.

- F7: RepoFixAgent.register_hook exposes process-local Python hooks to run_v3.
  Built-in policy/syntax/submit checks are registered in HookEngine; command
  hooks remain supported. Full-loop rejection and three-block cap tests PASS.
  CLI resume refuses callback-bearing manifests rather than silently dropping
  hooks; programmatic callers must re-register before continuing.

- F6: Prior verify behavior forced FAIL on any workspace change (a stricter
  deviation). It now restores the workspace, reports workspace_restored=true,
  and retains the verdict from test evidence. Passing-with-edits test PASS.

- F5: Verify command outputs are sanitized and capped at 1,000 characters;
  evidence is capped at 1,500 including its report prefix. Malicious role-like
  lines and long-output regression tested through the full loop. Pytest PASS.

- F4: Two responses without tools now terminate as no_tool_call, not interrupted.
  Resume rejects terminal runs before Docker initialization; patch collection
  errors are runtime_error. Reports enumerate terminal reasons. Full pytest PASS.

- F3: Exit 1 is benign only for the explicit search/comparison command list,
  after wrappers and using the final pipeline command; pytest/tox/python
  failures remain failures. Full pytest PASS.

- Scope: F1–F12, local commits on v3; no live models or HOLDOUT runs.
- F2: Successful edits now refresh read hashes, including Add/Move targets;
  Delete clears the old entry. Sequential edits succeed; external edits still
  require view. Full pytest PASS.
- F1: Docker edit payloads now use a temporary JSON archive transfer rather
  than command arguments. Real-helper tests edit a 310 KB file, apply a
  multi-file patch and verify rollback plus payload cleanup. Full pytest PASS.

## Original defaults and pre-existing state

- Default profile remains v1; V3 uses separate opt-in configuration.
- Preserve FINAL_CONFIG.md, task lists and all dev_artifacts byte-for-byte.
- Existing tracked deletions under runs/ were present before work and are left
  untouched and excluded from all milestone commits.
- Use WSL-native Git/Python. Added pytest 9.1.1; no new runtime dependency.
- No real model calls, SWE-bench tasks or HOLDOUT runs are authorized for V3.
- Non-root sandbox mode defaults off; performance/compatibility requires an
  explicitly enabled Docker check later.
- User permits one reset card below 2% quota; use only if an actual platform
  reset operation is available. Never claim a reset without confirmation.

## First-round milestones (historical record; corrections follow below)

### M0 — complete

- Recorded `tests/fixtures/v1_golden.json` on the unchanged 3fe3c0e loop,
  then mechanically extracted it into `harness/v1.py`: state holder, tool
  dispatcher and frozen registry. Public RepoFixAgent is now a facade routed by
  harness/loop.py. Both CLI entrances accept --profile; default is v1.
- Added FakeModelClient and in-memory FakeEnv. Golden requests include view,
  replace, bash, actual truncation, BM25 search and submit; all request messages,
  schemas and result counters match exactly. Only clocks/artifact path normalized.
- Tests: 27 passed, 1 Docker skip, 5 subtests passed.
- Design choice: isolate legacy execution in a compatibility adapter to avoid
  silently applying V3 ordering/hooks to frozen runs. V3 uses serializable RunState.
- No outstanding M0 work; later profiles are built milestone by milestone.

### M1 — complete

- Added opt-in V3 loop, registry handlers, bounded read-only thread batches and
  ordered observations. Writes form barriers on both sides. Added grep with rg
  and grep fallback; output includes file/line and bounded matches.
- Tests: parallel elapsed time/order, mixed write barriers, grep glob/limit,
  complete offline V3 loop, v1 golden. 30 passed, 1 Docker skip.
- Initial coverage relied on fake execution; F9 adds real helper coverage.
  Read-only workers default 4 (project choice).

### M2 — complete

- Python/argv hooks implement Allow/Deny/Rewrite and submit Allow/Block;
  external exit 2 uses stderr as rejection. Other external errors fail closed.
- Syntax hook fingerprints dirty contents as well as git porcelain status so
  repeated edits to already dirty Python files are checked, including bash.
- Verification must pass after the latest edit; fourth unverified submit is
  forced and marked. Permission hook is wired to M8 rules when configured.
- Tests: 34 passed, 1 Docker skip; external protocol, rewrites, repeated syntax
  errors without rollback, validation order and bounded submission blocking.
- Default external-hook timeout 30s (project choice); hooks are trusted host code.

### M3 — complete

- Added Add/Update/Move/Delete text patches, four progressive context matching
  modes, all-files prepare, Python compilation and whole-transaction rollback.
- View stores full-file hashes. Existing-file edits require a current view;
  adds need none. Traversal, external symlinks and .git paths are rejected.
- Tests: 49 passed, 1 Docker skip; parser errors, four matching levels,
  atomic prepare/rollback, moves/adds/deletes, stale/unseen reads and escapes.
- Conservative defaults: ambiguous context rejects; deletes also require a
  current view. Intentional new patch files are staged so feature additions are
  included in git diff HEAD; unrelated bash-created temporary files remain out.

### M4 — complete

- V3 shell defaults to 120s (max 600s); timed-out jobs stay in the container,
  with output/status files under /tmp/repofix_jobs. Added poll and process-group
  kill tools, stable output headers and benign exit-1 annotation. V1 unchanged.
- Pager/color/unbuffered environment variables injected for V3 shell jobs.
- Tests: 52 passed, 2 Docker skips; timeout-to-background, poll/kill, completion,
  environment constants and output headers. Docker survival test is opt-in.
- Background processes last only as long as their container; resume does not
  resurrect processes (M7 will mark interrupted calls rather than replay them).

### M5 — complete

- Before requests, usage-anchored token estimates trigger old-output masking;
  raw outputs are redacted into local artifacts. Only when needed, a separate
  summary request creates a typed handoff. Program-owned files/test/plan/task
  facts override model fields; recent tool-call pairs remain complete.
- Consecutive-request debounce and three unsuccessful compactions terminate as
  context_exhausted. Summary tokens/cost count toward the same budget.
- Tests: 55 passed, 2 Docker skips; masking, actual summary, facts reinjection,
  pairing, usage anchor, debounce and exhaustion.
- Uses the allowed chars/3 fallback (no tokenizer dependency or vocabulary
  download). Summary schema is parsed JSON, not a new Agent action protocol.

### M6 — complete

- Optional update_plan validates statuses and at most one active step, stores
  state atomically and survives the M5 handoff. It is never a submit requirement.
- Tests: 56 passed, 2 Docker skips; validation and nonmutation on failure,
  plan reinjection already covered by M5. This plan-only test does not validate
  the broader harness implementation or Docker paths.

### M7 — complete

- Atomic step manifests reference checksummed workspace snapshots (binary diff
  plus untracked tar outside the container). Initially checkpoints were written
  before dispatch and after every tool result; F10 reduces this to step-end and
  mutating-tool checkpoints while preserving plan/read hashes and shared budget.
- Added CLI resume and runner --resume; new isolated container, snapshot restore,
  interruption observations for pending calls, no side-effect replay. Corrupt
  newest checkpoints fall back to the previous complete snapshot.
- Tests: 58 passed, 2 Docker skips; snapshot/unknown tool interruption, corruption
  fallback, crash/resume comparison with uninterrupted messages and budget.
- Local resume requires the source still at its saved HEAD. Background jobs
  are not revived. Snapshots refuse untracked .env files and known credentials.

### M8 — complete

- Added JSON/TOML allow/ask/deny prefix policy, quote-aware compound splitting,
  env/nohup/timeout wrapper removal, deny precedence. Eval ask is denied; local
  CLI may explicitly approve. Rules are independently switchable from hooks.
- V3 containers drop all capabilities, prohibit privilege gain, cap PIDs at 512,
  RAM at 4g and CPU at 2; network remains none. V1 create options unchanged.
- Tests: 66 passed, 2 Docker skips; quote/wrapper/prefix/ask decisions and mocked
  Docker create parameters. Non-root option remains off and Docker-unverified.
- Rules intentionally do not claim shell security: sh -c/python -c can bypass
  lexical prefixes; the container is the security boundary.

### M9 — complete

- Explore/verify reuse the V3 loop with independent messages/state, restricted
  registries, depth one and shared locked cost accounting. Explore limits are
  8/15/25; verify is 15. Initially only report evidence was cleaned; command
  outputs were not bounded/cleaned. F5 corrects this gap.
- Verify records executed commands and restores any /testbed edits using the
  snapshot adapter. Optional pre-submit verification is capped at two rounds.
- Tests: 69 passed, 2 Docker skips; child context isolation, tool restriction,
  report cleanup, verify workspace restoration, shared-budget stop and depth cap.
- Defaults: subagents=none; verify_on_submit=false. Multi experiment enables both.
  Verification is a behavioral guard, not isolation from arbitrary delayed shell
  side effects; disposable Docker remains the runtime boundary.

### M10 — complete

- Added validated bugfix/feature TaskSpec, separate public prompt payload and
  judge-only hidden patches. CLI --task aliases --issue, with --kind feature.
- Builder compares real base/merged Git commits, extracts only test diffs,
  evaluates identical collected nodes at both revisions and requires human
  leakage review afterward. Placeholder-only YAML seed template included.
- Judge uses a new clean sandbox, production-only Agent patch then hidden tests;
  F2P/P2P must all pass, skipped tests do not count as passes.
- Tests: 73 passed, 2 Docker skips. Local two-commit end-to-end builder test runs
  pytest offline; FakeEnv covers judge logic and public/hidden separation.
- Default judge is pytest-node based; other test runners are a documented future
  adapter. YAML uses existing SWE-bench PyYAML; JSON works without it.

### M11 — complete

- Sequential experiment variants v1/v3-single/v3-multi/v3-nocompact, repeats,
  per-run JSONL metrics/artifacts, judge results and Markdown report/matrix.
- Fake mode never constructs Docker/provider clients; it runs scripted root and
  child loops, applies saved patches to a fresh FakeEnv and judges the outcome.
- Tests: 75 passed, 2 Docker skips; full four-variant fake pipeline, actual patch
  application, child adoption, report aggregation and pre-run HOLDOUT rejection.
- Fake reports are prominently labeled and cannot be mistaken for model scores.
  v1 feature comparisons intentionally use the frozen bugfix prompt with the
  public feature description; only V3 receives the feature workflow prompt.

### M12 — complete

- Added explicit termination enum, per-request tokens/model latency, per-tool
  duration/batch size, hook/permission decisions, child usage/latency and summary.
  Missing cache usage remains null. Budget exhaustion suppresses tool execution.
- V3 retains auxiliary reproduction telemetry, including across checkpoints.
  External hooks can be configured for all three events through an argv JSON file.
- Tests: 79 passed, 2 Docker skips; telemetry structure, cache absence and
  over-budget tool suppression in addition to all milestone checks.
- Accounting includes child/summary model requests. Estimated prices inherit v1
  constants and are not claims about current provider billing.
- Cross-cutting checks added: offline tests block socket connections; external
  post/submit protocols work; rewritten shell arguments are rechecked by policy;
  quoted mentions of pytest are not treated as verification commands.

### M13 — complete

- DESIGN.md documents each mechanism as problem/design/reference/difference/
  validation/limitations. README adds profile/feature/experiment/resume/policy/
  hooks usage without changing historical results. V3_CONFIG.md lists defaults
  and distinguishes task-book/project settings from public product mechanisms.
- Added CLI JSON config overrides, final profile validation, durable custom-task
  resume metadata and snapshot transport that avoids shell argument-size limits.
- Cross-milestone tests cover a feature with new production/test files, two-round
  verifier rejection, real isolated worktree patch materialization, switch-off
  configuration, redaction, and execution of the actual container helper source
  in redirected disposable local Git fixtures (no Docker/network).
- Final tests: **88 passed, 2 Docker skips, 5 subtests passed**. V1 golden PASS.
  Source/scripts compile check and CLI help/resume help PASS.
- Actual command smoke: four variants × two repeats with --fake completed;
  results/report at `.cache/v3-final-smoke/`. These are synthetic pipeline tests,
  not a real model capability or retrieval-quality result.
- Final integration fixes are included here rather than rewriting completed
  milestone history. Later independent review found the defects enumerated in
  F1–F12; the first-round passing tests did not prove those paths correct.

## First-round status at dd15192

- M0–M13 locally committed on `v3`, one commit per milestone. Later pushed to
  origin/v3 on explicit user request; not merged into main.
- Real Provider calls in this task: **0**. HOLDOUT runs in this task: **0**.
  Historical final-evaluation counts/artifacts remain unchanged.
- Protected files (FINAL_CONFIG.md, tasks.txt, holdout.txt, dev_artifacts/) match
  base 3fe3c0e. README preserves all original content and appends V3 only.
- Secret scan / redaction tests: PASS. `git diff --check`: PASS.
- Preserve the **20 pre-existing tracked runs/ deletions** unstaged; they are not
  V3 changes and are excluded from every commit. No V3 implementation is left
  uncommitted after M13.
- Quota checked during the task: 24% used / 76% remaining; no reset card used.

## Remaining user-run validation

1. Optional Docker integration, including background jobs, full resume and
   non-root ownership compatibility. Default tests intentionally skip Docker.
2. Supply real trusted feature PR/commit pairs; replace template placeholders,
   run the builder and manually review descriptions for implementation leakage.
3. Run real model experiments only when desired. This task did not consume an
   API key, make Provider calls or produce new benchmark claims.

Commands (WSL, activate the existing RepoFix Python 3.12 environment first):

```bash
python -m pip install -r requirements-dev.txt
python -m pip install -e .
pytest -q
python scripts/run_experiment.py --tasks tasks/fake_tasks.json \
  --variants v1,v3-single,v3-multi,v3-nocompact --repeats 1 \
  --out .cache/my-new-fake-run --fake
python scripts/report.py --input .cache/my-new-fake-run/results.jsonl \
  --out .cache/my-new-fake-run/report.md

# Optional, explicitly enabled Docker tests:
pytest -q --docker tests/test_v3_shell.py
REPOFIX_DOCKER_INTEGRATION=1 pytest -q --docker tests/test_local_sandbox_integration.py

# After replacing the seed placeholders with real reviewed evidence:
python scripts/build_feature_tasks.py --input /path/to/real-seed.yaml \
  --out /path/to/tasks.json --execute
# Manually confirm public descriptions do not leak implementations, then:
python scripts/run_experiment.py --tasks /path/to/tasks.json \
  --variants v1,v3-single,v3-multi,v3-nocompact --repeats 1 --out /path/to/new-run
repofix --repo /path/to/trusted-repo --task "Describe the feature" --kind feature --profile v3
repofix resume /path/to/run-directory
```

## Second-round final status and specification deviations

| Item | Before review fix | After fix / evidence |
| --- | --- | --- |
| F1 | Whole file embedded in command argument | Archive JSON payload; 310 KB real-helper edits and rollback |
| F2 | Own edits invalidated read hashes | Refresh successful edits, Add/Move; external changes still rejected |
| F3 | pytest exit 1 marked benign | Command-word allowlist; failing tests remain failures |
| F4 | No-tool stop treated as interruption | no_tool_call; terminal resume rejected before container creation |
| F5 | Raw verify command output returned | Cleaned 1000-char command output; 1500-char evidence |
| F6 | Any restored edit forced FAIL | Restore + explicit flag; verdict follows executed tests |
| F7 | Python callbacks not reachable from run | Public register_hook; built-ins registered in HookEngine |
| F8 | 2>&1 split; substitutions ignored | Redirect-aware splitting; recursive substitution checks |
| F9 | Critical backend branches mostly untested | Real local grep fallback, background workers, edit helpers tested |
| F10 | Repeated events/snapshots/index builds | New events only; bounded checkpoints/backoff; shared child index |
| F11 | State-bag locals, monkey patch, giant lines | core.py, explicit V1Loop dependency, cohesive tool modules, formatter |
| F12 | Overstated parity; inaccessible masked files | Honest limits; redacted /tmp copies and resume rehydration |

- F1–F12 each has a local `v3-fix(Fn)` commit on v3. No live models, no HOLDOUT
  runs and no new performance claims. No quota reset used in this repair round.
- Remaining deliberate deviations: F9 takes the task's allowed helper-test
  alternative instead of replacing all in-memory branches; F12 uses flat
  `/tmp/repofix_artifact_tool-*.txt` paths to retain the existing file-write API.
- V1 golden remains unchanged and passes. Full offline suite: **119 passed,
  2 Docker skips, 5 subtests passed**. Source line bounds, top-level imports,
  formatting, compile checks, secret/redaction audit and diff whitespace PASS.
- Four variants × two repeats were exercised through the repaired fake runner;
  report: `.cache/v3-fix-final-smoke/report.md`. Synthetic fixture results only.
- No protected frozen config/task list/dev_artifacts changes. Original 20 tracked
  runs/ deletions remain unstaged and outside every repair commit.

Still unverified: real Docker put_archive transfer for >300 KB payloads, Docker
full checkpoint/resume, non-root ownership under dropped capabilities, real
Provider behavior and real feature-task quality. Local helper tests do not prove
these integration paths. The optional Docker suite currently covers shell job
survival and editable sandbox imports; it is not complete container coverage.

User-run commands (WSL, trusted repositories/images only):

```bash
source /home/jiusi/venvs/repofix/bin/activate
python -m pytest -q
# Explicit Docker opt-in; no model key is needed for these integration tests.
REPOFIX_DOCKER_INTEGRATION=1 python -m pytest -q -m docker --docker
# Equivalent selection is pytest -m docker; --docker is required by our guard.

# Fill 10–20 real tested feature PR pairs; manually review leakage after build.
python scripts/build_feature_tasks.py --input /path/to/reviewed-feature-seed.yaml \
  --out /path/to/feature-tasks.json --execute
# Configure your own key in ignored .env; never put its value on the command line.
python scripts/run_experiment.py --tasks /path/to/feature-tasks.json \
  --variants v1,v3-single,v3-multi,v3-nocompact --repeats 1 \
  --out /path/to/new-real-experiment
python scripts/report.py --input /path/to/new-real-experiment/results.jsonl \
  --out /path/to/new-real-experiment/report.md
```
