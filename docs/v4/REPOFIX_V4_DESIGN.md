# RepoFix：可验证、可恢复的 Coding Agent Harness

## 1. 架构与边界

问题：一次工具调用成功、模型说完成、本地测试通过、任务真正修复，是四件不同的事。
旧接口保留，V4 通过显式 profile 在同一个 loop/runtime 上补充证据与故障语义，
不复制另一个 Agent，不把小样本开发题包装为泛化 benchmark。

```text
RepoFixAgent.run → run_v3 → drive → Runtime.execute → 原有工具 / run_tests
                         │          │
                         │          ├─ ValidationRunner → EvidencePolicy
                         │          └─ HookEngine / DockerEnv / JobManager
                         ├─ ProgressMonitor：WARN → REPLAN → STUCK
                         └─ V4CheckpointStore：state + workspace generation
候选 full patch → production-only evaluation patch → 独立官方 SWE-bench Judge
```

- Model 决定动作；Harness 记录实际执行及预算；Judge 独立验收。不能共享一个 success 布尔值。
- `agent.py` 是入口；`harness/loop.py` 负责消息协议与边界；`runtime.py` 负责调用；
  `env.py` 提供 Docker 执行。V4 的工具、状态和配置开关只在 v4 profile 默认启用。
- v1 保留默认入口，v3 的工具 schema 和配置序列化保持兼容。M0 冻结原生 schema 哈希，
  每个里程碑都运行原有回归；历史冻结文件和实验目录不改。
- V4 继承 V3 已有 Context、Hooks、只读工具调度和 checkpoint，不声称这些机制是本轮新做的，
  也没有单独证明压缩/并行/子 Agent 带来了任务收益。

## 2. 可信本地验证

问题 → shell 管道的最后一个退出码、`echo pytest passed`、子 Agent 的 PASS 都可能造成伪成功。

机制 → `validation.py::ValidationRunner` 只接受受限 runner/targets/args，通过
`DockerEnv.execute_argv` 直接执行 pytest，不经 shell 拼接。宿主生成唯一 JUnit 路径，
解析实际 testcase 数、失败、错误、跳过，保存完整 stdout/stderr/XML 和证据哈希。
`EvidencePolicy` 只消费内部登记的证据，重新检查当前工作区指纹。

- PASS 必须实际完成、exit=0、非超时、tests>0、passed>0、无失败/错误、工作区前后相同。
- 空测试、全跳过、缺少报告、未知退出码、运行器环境错误：INCONCLUSIVE；确定的测试失败：FAIL。
- 跟踪文件、非忽略的 untracked 文件内容、模式、链接目标及 HEAD 参与版本绑定。
  仅忽略未跟踪的 `__pycache__`/`.pytest_cache`，不忽略已跟踪文件。
- PASS 后再编辑 → STALE。普通 bash 只提供执行/复现辅助遥测，不制造证据。
- 三次拒绝之后第四次可以输出候选 patch，但明确 `submit_forced/UNVERIFIED`。
  子 Agent 的建议和普通 hook 文案不能升级为本地 PASS 或 Judge RESOLVED。

失败语义 → 本机官方 Django 镜像实际缺少 pytest，不能为了 Django 把文字输出改成可信 PASS。
保留可运行的原生测试供模型诊断，结构化状态诚实为 INCONCLUSIVE/UNVERIFIED。

成本/取舍 → 工作区内容签名、独立报告与完整输出有 IO 成本；它验证的是所运行测试，
不是需求覆盖。目标代码/pytest 插件被信任，容器 root 可以伪造报告的对抗场景不在安全承诺内。

实际验收 → M1 Docker pass/fail/skip/empty/timeout；M5 独立夹具明确本地 PASS + 隐藏测试失败。
测试：`test_v4_validation*`、`test_v4_run_tests.py`、`test_v4_reliability_docker.py`。
尚未验证 → 多语言 runner、恶意仓库对抗、测试覆盖充分性。

## 3. 有限无进展纠偏

问题 → 仅靠 50 步预算允许同样的 grep/失败循环；仅按命令重复判断又会误伤修后重测。

机制 → `progress.py::ProgressMonitor` 复用文件阅读版本、Hook 工作区指纹、实际工具输出。
归一化 UUID/时间/耗时，计划文字变化不算进展。代码变化、新文件/版本和新测试结果是新证据。
重复 3 次 WARN，连续 8 个无进展步 REPLAN，最多 2 次，再耗尽进入 STUCK；均可配置。
真实 RUNNING 的 job 有有限 20 次轮询额度，另受任务总截止控制。

失败语义 → STUCK 保存预算、完整 patch 和 trace，但不伪称 submitted/resolved。
成本/取舍 → 不每步重建 BM25；逐次记录监测耗时和反馈字节。阈值是工程首值，不是最优参数。
实际验收 → 重复 grep 有限退出；改代码后同一测试、不同文件/新版本、真实后台等待不会误停。
测试：`test_v4_progress.py`、`test_v4_progress_docker.py`。
尚未验证 → 长程语义进展；读新文件不一定是有效研究，本监测不是正确性判定器。

## 4. 状态与工作区配对恢复

问题 → 只写状态 JSON 无法保证它与工作区对应；执行中断后重试写操作可能重复副作用。

机制 → `checkpoint_v4.py::V4CheckpointStore` 在原有 store 上提供 schema=4，单调 generation。
每代绑定 RunState 哈希与内容寻址 workspace blob；原子写、fsync 和加载校验后才信任。
同一步可有 pre_dispatch、post_mutation、end_step 多代。损坏最新代则回退完整旧代。

- pre_dispatch 必须保存 assistant + pending_calls；失败则不执行工具。
- 新进程恢复 pending 为唯一 `INTERRUPTED_UNKNOWN` ToolMessage；不自动重放未知副作用。
- 保留步数/费用，验证证据重新比对工作区；旧 job 为 LOST_AFTER_RESTART。
- 最多 64 MiB 未压缩 snapshot、最近 4 个完整 manifest；只删除无有效引用的旧 blob。
- tar 路径逃逸、凭证、非 regular 内容和超限被拒绝。仅恢复可丢弃容器 `/testbed`，
  不对用户宿主仓库执行 reset。
- Python 3.6 兼容只转换宿主自有 helper 模板；使用 UTF-8 locale，目标源码不被转换。

失败语义 → checkpoint_error/snapshot_refused 明示，磁盘不足不继续冒险写工具。
成本/取舍 → 双内容签名是乐观一致性检测，不是能抵抗恶意并发写者的文件系统原子快照。
状态/工作区 blob 的审计副本保持原样在本地，公开脱敏副本不能当作可恢复快照使用。
实际验收 → 6 个真实独立 Python 进程 SIGKILL 故障点，退出 -9，另起进程恢复 exit 0；
实际 Django Python3.6 编辑/语法回滚/恢复通过。初次失败根因和日志未删除。
测试：`test_v4_checkpoint.py`、`test_v4_resume_docker.py`、`v4_process_fixture.py`。
尚未验证 → 断电/磁盘硬件故障、跨机器恢复、任意大仓库性能。

## 5. 执行与沙箱生命周期

问题 → 管道出口 0、后台进程仍活着、SDK 卡住或 Docker 清理失败会让结束状态失真。

机制 → V4 前台/后台 shell 均 pipefail，保存真实退出码；SIGPIPE 141 明确解释。
EXITED/TIMEOUT/RUNNING/CANCELLED/TOOL_ERROR/ENV_ERROR 分开；benign_exit 仅是展示提示。
默认后台任务最多 2；wall deadline=1800 秒，恢复等待也计入截止时间。
请求超期的晚响应不执行工具；未知用量记 null。退出时停止已追踪 process group，
runner/context manager 移除所属容器，失败留下 cleanup_error。

实际 inspect → network=none、4GiB、2 CPU、PIDs=512、cap_drop=ALL、no-new-privileges。
成本/取舍 → Python 不能强杀 SDK 请求线程；晚线程可能等自身 timeout 才退出。
截止后有有限清理/patch 收集宽限，不是硬实时。默认 root、不支持多租户恶意代码托管。
构建期联网和依赖安装执行目标代码，所以只运行可信仓库。
实际验收 → 真后台 job 轮询/kill/上限；提交、中断、Provider 异常、deadline 四路径清理。
测试：`test_v4_shell.py`、`test_v4_shell_docker.py`。
尚未验证 → Docker daemon 崩溃后的自动修复、操作系统级强制请求取消。

## 6. Trace 与独立评测

问题 → Fake 数字、模型建议和实际事实混在一起，容易形成无法核查的“成功率”。
机制 → `reliability.py` 标记 producer/backend/code/config；`evidence.py` 从真实 JUnit/JSONL
生成故障矩阵，缺测保留 null/NOT_MEASURED；每指标有自己的分母。
`scripts/run_v4_dev.py` 只加载选中 DEV 的公开字段；官方 Judge 独立进程运行，无 Provider key，
不回灌给 Agent。保留 full patch 与 production-only evaluation patch。

成本/取舍 → 模型费用按已有固定价格与响应 usage 估算，不是账单，SDK 内部 retry 用量未必可知。
只用三个已知 DEV 各一次，不能外推泛化能力；结果不触发代码/阈值/Prompt 调整。
实际验收与失败 → 见 `REPOFIX_V4_EVIDENCE_REPORT.md`，机器原始来源见 manifest。
源码学习 → `V4_SOURCE_WALKTHROUGH.md` 按最终代码真实行号索引。
