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
