# RepoFix Step 3.2 final evaluation

All Agent runs used frozen commit
`42db5504f8a86b951ad02950db67a67e47c3d6cd`. No task was rerun and no
configuration changed between runs. Official Harness scoring used the saved
production-only evaluation patches.

## FINAL_DEV_A

| Task | Result | Terminal | Steps | Cost | Wall | Genuine pre-fix repro / telemetry | Search | Production/non-test files | Test files | Submitted |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- | --- | --- |
| `django__django-16429` | RESOLVED | submitted | 10 | $0.002471088 | 23.858s | YES / YES | 0 | `django/utils/timesince.py` | — | YES |
| `django__django-15277` | RESOLVED | submitted | 14 | $0.003568692 | 44.259s | YES / YES | 0 | `django/db/models/fields/__init__.py` | — | YES |
| `django__django-13343` | RESOLVED | submitted | 20 | $0.005834256 | 49.252s | YES / YES | 1 | `django/db/models/fields/files.py` | — | YES |
| `django__django-16454` | RESOLVED | submitted | 32 | $0.011487756 | 244.667s | YES / YES | 0 | `django/core/management/base.py` | `tests/user_commands/tests.py` | YES |
| `django__django-16950` | RESOLVED | max_steps | 50 | $0.024510720 | 267.151s | YES / YES | 0 | `django/forms/models.py`, `docs/releases/4.0.1.txt` | `tests/model_formsets/test_uuid.py` | NO |

Run A totals: 5/5 resolved; 126 Provider calls; 143 tool calls; prompt/cache-hit/
completion tokens 1,316,792 / 1,277,952 / 23,794; cost $0.047872512;
Agent wall 629.187s; Harness wall 116.12s.

## FINAL_DEV_B

| Task | Result | Terminal | Steps | Cost | Wall | Genuine pre-fix repro / telemetry | Search | Production/non-test files | Test files | Submitted |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- | --- | --- |
| `django__django-16429` | RESOLVED | submitted | 10 | $0.002483652 | 23.161s | YES / YES | 0 | `django/utils/timesince.py` | — | YES |
| `django__django-15277` | RESOLVED | submitted | 13 | $0.003454956 | 32.617s | YES / YES | 0 | `django/db/models/fields/__init__.py` | — | YES |
| `django__django-13343` | RESOLVED | submitted | 19 | $0.006099312 | 49.798s | YES / YES | 1 | `django/db/models/fields/files.py` | `tests/file_storage/tests.py` | YES |
| `django__django-16454` | RESOLVED | submitted | 38 | $0.013917852 | 87.168s | YES / NO | 0 | `django/core/management/base.py` | `tests/user_commands/tests.py` | YES |
| `django__django-16950` | RESOLVED | max_steps | 50 | $0.028523064 | 121.298s | YES / YES | 0 | `django/forms/models.py` | `tests/model_formsets/test_uuid.py` | NO |

Run B totals: 5/5 resolved; 130 Provider calls; 140 tool calls; prompt/cache-hit/
completion tokens 1,410,251 / 1,369,856 / 28,451; cost $0.054478836;
Agent wall 314.042s; Harness wall 120.97s.

Human trajectory review found genuine issue reproduction before production edits
in all ten DEV attempts. The automatic telemetry missed Run B 16454 because its
reproduction printed the bad behavior while exiting zero. Both runs resolved all
five tasks, so both-resolved = 5 and unstable task count = 0. Both 16950 attempts
produced correct production patches but reached 50 steps without `submit`.

## Three sealed final holdout tasks

| Task | Result | Steps | Cost | Genuine pre-fix repro / telemetry | Submitted | Failure type |
| --- | --- | ---: | ---: | --- | --- | --- |
| `django__django-11099` | RESOLVED | 8 | $0.001759776 | YES / NO | YES | — |
| `django__django-12713` | RESOLVED | 16 | $0.005784336 | YES / YES | YES | — |
| `django__django-11477` | UNRESOLVED | 30 | $0.011382516 | YES / YES | YES | implementation reasoning |

Holdout total: 2/3 resolved; 54 Provider calls; 57 tool calls; prompt/cache-hit/
completion tokens 337,679 / 319,488 / 9,627; cost $0.018926628;
Agent wall 597.231s; Harness wall 71.49s.

The unresolved task localized and reproduced the optional URL-parameter symptom,
but fixed `translate_url()` rather than the underlying resolver behavior. Official
tests showed two required resolver cases still retained optional named groups as
`None`. The focused validation did not cover that abstraction boundary. This is
recorded permanently without a system change or rerun.

## Aggregate

- Prompt/cache-hit/completion tokens: 3,064,722 / 2,967,296 / 61,872
- Prompt + completion tokens: 3,126,594
- Estimated cost: $0.121277976
- Agent wall time: 1,540.460s
- Harness wall time: 308.58s
- `HOLDOUT_STATUS = UNSEALED_FOR_FINAL_EVALUATION`
- `HOLDOUT_AGENT_RUNS = 3`

Each run directory here contains the batch summary, official Harness report, full
patches, and production-only evaluation patches. Canonical trajectories are in
`dev_artifacts/trajectories/final-dev-a`, `final-dev-b`, and `final-holdout`.
