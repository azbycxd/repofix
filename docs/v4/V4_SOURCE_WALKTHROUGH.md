# 源码学习索引

运行时代码版本：`c5d868772feb0baf760e189204c1b6948f786f98`。
行号在本次修改完成后实际检索；M6 只增加报告/导出脚本，不调整被测运行时。
先读证据再读实现，避免把接口存在当成机制已验证。

| 顺序 | 入口 / 实际行号 | 跟踪的确定性事实 | 验证入口 |
|---|---|---|---|
| 1. Tool Execution | [agent.py:90](../../src/repofix/agent.py#L90) → [loop.py:31](../../src/repofix/harness/loop.py#L31) / drive:65 → [runtime.py:275](../../src/repofix/harness/runtime.py#L275) | assistant tool_call_id、调度、执行结果、role=tool 回填；pre_dispatch 必须早于执行 | test_v4_profile；test_v4_shell；M6-all-docker JUnit |
| 2. Local Validation | [validation.py:204](../../src/repofix/harness/validation.py#L204) run:210 → EvidencePolicy:332/current:336 → submit tool | 宿主唯一 argv/report、工作区前后签名、PASS/FAIL/INCONCLUSIVE；提交不等于 Judge | test_v4_run_tests.py:61 的真实 subprocess；test_v4_reliability_docker.py:17 的独立隐藏失败 |
| 3. Progress | [progress.py:46](../../src/repofix/harness/progress.py#L46) / observe:64 → loop drive | 什么是新文件/版本/结果；WARN、REPLAN、STUCK；自然语言不算进展 | test_v4_progress.py:29 重复 grep；:68 不同文件；:82 修后重测；Docker waiting |
| 4. Checkpoint | [checkpoint_v4.py:143](../../src/repofix/harness/checkpoint_v4.py#L143) save → load:247 / resume:265 → [resume.py:21](../../src/repofix/harness/resume.py#L21) | generation/hash/state+snapshot 配对；pending 唯一 UNKNOWN；预算保留，旧 job LOST | test_v4_resume_docker.py:40，六个实际 SIGKILL 参数；:138 拒绝快照；:169 Python3.6 |
| 5. Sandbox | [env.py:145](../../src/repofix/env.py#L145) execute / execute_argv:211 → [shell.py:40](../../src/repofix/harness/tools/shell.py#L40) start / cleanup:194 | pipefail、后台状态、进程组回收、Docker 资源边界；运行/验证/总截止不同 | test_v4_shell_docker.py:20 管道；:39 inspect/job；:78 终止 cleanup |
| 6. Eval & Trace | [reliability.py:31](../../src/repofix/harness/reliability.py#L31) write → [telemetry.py:50](../../src/repofix/harness/telemetry.py#L50) finish → [run_v4_dev.py:30](../../scripts/run_v4_dev.py#L30) | producer、local/submission/Judge 分离；公开字段 Agent 输入与独立官方判分进程 | docs/v4/evidence 的真实 JUnit、DEV trajectory、official-judge stdout 和逐题 report |

## 建议逐步复核

1. `evidence/matrix-m5/summary.json` 先看故障指标分母，再读 `fault_results.jsonl` 的对应
   testcase/properties。没有 properties 的用例不被补成指标 0。
2. 真进程恢复：`evidence/traces/M6-all-docker/test_real_process_kill_and_ind0/fault.json`
   看 `process_exit=-9`、`resume_exit=0`、签名一致和旧容器移除；同目录的 resumed JSONL
   对照 tool_call_id。0..5 对应 pre_dispatch、post_edit、mid_write、active_job、坏 manifest、坏 blob。
3. 验证边界：`test_local_pass_is_not_independent_judge_resolution` 的原始
   `independent-judge.json`，确认隐藏测试 False 不会反向更改宿主本地 PASS 的历史事实。
4. 真实 DEV：按 `evidence/dev-final/manifest.json` 核对代码、模型、配置；先读每题
   trajectory 的 config → tool_execution → validation/progress/checkpoint → summary。
   再看 evaluation.patch 和官方 report，不用 submit 推导 resolved。
5. 原样可恢复 blob 在本地 `runs/v4-reliability-20261010/`。文档目录是脱敏审查副本；
   manifest 同时保存原始 SHA 和发布 SHA，不能拿改过敏感路径的 JSON 当恢复输入。

本次真实 DEV16429 可直接跟随：第 7、16 步 `run_tests` 返回 pytest 缺失的 INCONCLUSIVE；
第 13 步 pip 安装被权限规则拒绝；第 17 步 submit 被验证门拒绝；第 18 步最终
`no_tool_call`。这条轨迹适合学习“执行成功/验证未知/尝试提交/是否接受/官方 Judge”
为何必须分开。官方结果只在 Judge 报告中读取，不从这些动作反推。

## 自己复跑机制，而不是重刷开发题

```bash
python -m pytest -q tests/test_v4_run_tests.py tests/test_v4_progress.py tests/test_v4_checkpoint.py
python -m pytest -q --docker tests/test_v4_resume_docker.py tests/test_v4_reliability_docker.py
python scripts/run_v4_reliability.py --output runs/<全新目录> --docker
```

这些 Docker 故障用 FakeModel，但真实执行容器与 kill/resume。它们不是付费模型修题数字。
这份交接不授权 HOLDOUT、重新调参或重复 DEV 刷分；真实评测原始结果永久保留。
