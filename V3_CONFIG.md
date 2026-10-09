# RepoFix V3 configuration

Default CLI profile is **v1**. V3 defaults mean
`HarnessConfig.for_profile("v3")`, not merely a dataclass field override.
`--config path.json` supplies named dataclass overrides; `--profile` stays on the
command line. No credentials belong in configuration files.

All numeric V3 settings below are task-book/project choices. Public Codex/Claude
mechanisms inspired the design, but none of these numbers is asserted to be a
current product default. Sources and distinctions: [DESIGN.md](DESIGN.md).

| Parameter | v1 | v3 default | Source / purpose |
| --- | --- | --- | --- |
| model | deepseek-flash | same | frozen project baseline |
| base_url | https://api.deepseek.com | same | existing provider |
| temperature / thinking | 0 / disabled | same | frozen baseline |
| max_steps / max_cost_usd | 50 / 0.5 | same | frozen baseline; root shared cost |
| max_completion_tokens | 8192 | same | frozen baseline |
| provider_timeout_seconds | 300 | same | frozen baseline |
| SDK max_retries | 2 | same | frozen client |
| retrieval_mode | bm25 | bm25 | position-only initial repository index |
| reviewer_enabled | false | false | frozen default; no V2 Reviewer in V3 |
| tool_output_max_chars | 12000 | 12000 | frozen truncation threshold |
| tool_output_head_chars / tail_chars | 6000 / 6000 | same | frozen truncation slices |
| tool_timeout_seconds | 60; kills on timeout | legacy field; V3 shell uses 120 by default, max 600 | task book M4 |
| parallel_readonly | false | true | task book; contiguous read batches |
| readonly_workers | unused | 4 | project bounded thread pool |
| hooks_enabled | false | true | task book M2 |
| hooks_file | none | none | trusted argv hooks, 30s default timeout |
| apply_patch_enabled | false | true | M3 |
| read_before_edit | false | true | view hash required for existing edits |
| background_shell | false | true | M4 |
| context_management | false | true | M5 |
| context_window | unused | 64000 | task book experimental window |
| compact_threshold | unused | 0.75 | task book |
| keep_recent_tool_results | unused | 4 | task book; complete recent tool groups |
| summary attempts / cooldown | unused | terminate after 3 over-limit results; skip next request | task book |
| token fallback | unused | characters / 3 | task book; no optional download |
| plan_tool | false | true | M6, not a gate |
| checkpointing | false | true | M7 |
| permissions_enabled | false | true | independent of hooks |
| permissions_file | none | none; built-in policy | M8 |
| permission_default | unused | allow | project default, explicit denies below |
| evaluation_mode | unused | true in runners; false in local CLI | ask rejects in evaluation |
| sandbox_hardening | false | true | M8: ALL caps dropped, no-new-privileges |
| container limits | existing v1 | 512 PIDs / 4g / 2 CPUs | task book |
| sandbox_user | root | none (root) | optional non-root unverified; see DESIGN |
| subagents | none | none | explicitly choose explore/verify/both |
| explore steps | unavailable | quick 8 / medium 15 / thorough 25 | task book |
| verify steps / depth | unavailable | 15 / max depth 1 | project choice / task book |
| verify_on_submit | false | false | v3-multi experiment sets true |
| verification_patterns | empty | empty | optional regex command patterns |
| submit blocks / child verification rounds | unused | 3 / 2 | task book |
| task_kind | bugfix only | bugfix; CLI --kind feature supported | task book |

Estimated USD per million tokens inherits the v1 constants: cache-hit input
0.006, cache-miss input 0.30, output 1.20; pricing_source retains the existing
DeepSeek pricing URL. These estimates were not revalidated against current billing
because this implementation task makes no real Provider calls.

Built-in command policy denies `git push`, `rm -rf /`, `curl`, `wget`; asks for
`pip install` and `python -m pip install`; otherwise allows. Token prefixes are
not a security boundary. Shell interpreter code can evade lexical matching.

Variants:

- `v1`: frozen prompt/tools/limits/truncation/evaluation patch behavior. V3 flags
  and altered inherited model/limit values are rejected for this profile.
- `v3-single`: V3 defaults, subagents none.
- `v3-multi`: V3 defaults, subagents both and verify_on_submit true.
- `v3-nocompact`: v3-single with context_management false.

Example opt-in overrides (all mechanisms remain individually configurable):

```json
{
  "subagents": "both",
  "verify_on_submit": true,
  "context_window": 64000,
  "parallel_readonly": true
}
```

Resume reads the saved configuration from resume.json, not current CLI defaults.
Never place API keys or .env contents in config, hooks files or task descriptions.

Second-round implementation notes (no model/limit changes):

- Python hooks: `agent.register_hook("PreToolUse", callback)` (or PostToolUse /
  PreSubmit), before run; callbacks are process-local, not JSON configuration.
  CLI resume rejects callback-bearing manifests; programmatic resume must
  re-register them. Built-in checks are registered when hooks_enabled is true.
- Checkpoints: after assistant/pending_calls, before tool dispatch; at step end;
  and after bash/str_replace/apply_patch/verify (not after readonly tools).
- Job polling: 0.1, 0.2, 0.5, then 1 second; tail-only while waiting.
- Verification evidence: command outputs <=1000 chars, evidence <=1500 chars,
  including prefixes. Restoring child edits does not override the test verdict.
- `no_tool_call` is terminal, not resumable; `runtime_error` is not interruption.
- Masked outputs: redacted host archives plus model-readable direct /tmp files;
  a recreated container receives copies from the same local run directory.
