# RepoFix final frozen configuration

The commit containing this file is the immutable configuration used for the
Step 3.2 final DEV and sealed HOLDOUT evaluation.

- Agent architecture: single native tool-calling loop
- Model: `deepseek-flash`
- Temperature: `0`
- Thinking: disabled
- Maximum steps: `50`
- Estimated cost cap: USD `0.5`
- Tools: `bash`, `view`, `str_replace`, `search_code`, `submit`
- Retrieval: BM25 enabled, position-only results
- Dense/RRF: disabled
- Reproduction-first: enabled
- Tool-output truncation: enabled, 12,000 characters (6,000 head + 6,000 tail)
- Evaluation patch: production-only; test paths remain in full trajectory diff
- Reviewer: disabled
- Provider SDK retries: `2`

After this freeze, final evaluation results do not authorize changes to
`agent.py`, prompts, tools, retrieval, reproduction-first policy, model/limits,
or evaluation-patch policy. Tasks are run once per scheduled evaluation slot;
failures are recorded and analyzed without tuning or reruns.
