# RepoFix V3 progress

Date: 2026-10-09 (Asia/Shanghai). Branch: `v3`. Base: `3fe3c0e`.
Scope: REPOFIX_V3_TASK.md M0–M13, offline only, no push or merge.

## Defaults and pre-existing state

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

## Milestones

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
- No deviations or outstanding work. Read-only workers default 4 (project choice).

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
  plan reinjection already covered by M5. No deviations.

### M7 — complete

- Atomic step manifests reference checksummed workspace snapshots (binary diff
  plus untracked tar outside the container). Checkpoints before dispatch and
  after tool results preserve pending calls, plan/read hashes and shared budget.
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
  8/15/25; verify is 15. Only bounded, role-marker-cleaned reports return.
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
