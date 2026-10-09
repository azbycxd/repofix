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
