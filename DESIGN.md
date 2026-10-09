# RepoFix V3 design

## Compatibility and loop (M0)

**Problem.** Long-task mechanisms must not change the historical v1 experiment.

**Design.** RepoFixAgent stays the public facade. harness/loop.py routes profiles;
harness/v1.py holds the mechanically extracted loop and registry-based tool
dispatch, with a local legacy state holder. V3 uses a serializable RunState.

**Reference / difference.** Native tool-call loop follows the general Codex
harness pattern. A separate compatibility executor is intentional: frozen error
strings, nudges, timeout and result accounting are part of the v1 contract.

**Validation.** Before refactoring, record a scripted offline session with all
five tools and a truncated observation; compare requests/messages and counters
afterward. Exclude nondeterministic clocks and artifact paths only.

**Limitations.** V1 remains synchronous and is not upgraded by V3 switches.
