# RepoFix V4 progress

2026-10-10（Asia/Shanghai）。隔离分支 v4，源 v3=e9f3157498d0d80197f9c6ae56df4c5bdc1273d6。
源工作区 20 个既存 tracked 删除及未跟踪任务书保留；上一轮独立实验目录保留。
不 push、不 merge、不运行或判分 HOLDOUT。真实 DEV 最后才运行，各一次、估算合计上限 $1。

## M0 — PASS / fed124694628eebf47125ea2814265babcaf13b4

- 新增 v4 profile，继承 V3 能力开关；仅 v4 开启 structured_validation、progress_monitor、
  checkpoint_limits、reliability_telemetry。本阶段只冻结合同，后续逐项实现其语义。
- CLI、SWE runner、resume 入口贯通；主循环复用 run_v3；旧 profile 配置输出剔除 V4 字段。
- V3 原生 schema 的 SHA256 在改造前记录并加入断言；原有 RunState 格式与 V1 golden 不改。
- 改造前全套离线测试、Docker version/images 的原始日志保留在
  runs/v4-reliability-20261010/commands/M0-*；新基线只存该新目录，不改旧实验。
- Key 存在性=true（不记录值）；Docker daemon 可用，三道指定 DEV 镜像及 python:3.12-slim 已存在。
- 改造前全量 132 passed / 2 skipped / 5 subtests；改造后 137 passed / 2 skipped / 5 subtests。
- 单独 V1 golden 1 passed、V3 acceptance 4 passed、V4 profile 5 passed，runner --help exit 0。
  确切 argv、时间和退出码见 M0_baseline.json；上述子集不重复加入全套总数。
- 本阶段没有真实 Provider 或 HOLDOUT 调用；仅确认环境，尚不宣称新验证/恢复语义实现。

## M1 — PASS / d8ddf4678ade2080bb6e13852c1835f13b9cbed5

- 新增宿主控制的 run_tests：直接 argv、UUID 报告、JUnit 实际 testcase 计数、
  全仓库内容指纹、证据/输出哈希；明确区分本地 PASS、候选提交和独立 Judge。
- bash 文案、管道外层 exit 0、子 Agent PASS 和模型提供的 report/metadata 均不能产生可信 PASS。
  代码变化使证据 STALE；三次阻断后的第四次提交明确 forced/UNVERIFIED。
- 离线目标检查 46 passed；真实 Docker 6 passed（99.79s pytest / 100.272s wall）。
  Docker 真实跑了 pass/fail/skip/empty/timeout 和掩盖失败的管道，不是 FakeEnv 替身。
- 第一版真实本地 pytest 发现未跟踪 __pycache__ 导致假 STALE；修正为仅忽略未跟踪
  Python/pytest cache，已跟踪文件仍逐字节参与签名。初次失败日志不覆盖，增加回归测试。
- 官方 Django 镜像没有 pytest：实际运行器检查 exit 1，明确记 ENV_ERROR/INCONCLUSIVE。
  正向 Docker 用独立可信 fixture 镜像；不改变官方 DEV 镜像或降低判据。
- 原始日志：runs/v4-reliability-20261010/commands/M1-*；完整 stdout/stderr/XML 与
  结构化证据：同目录下 traces/M1-*。没有真实 Provider/HOLDOUT 调用。
- 全量离线回归：171 passed / 8 skipped / 5 subtests。
- 边界：本地测试范围不代表 issue 已解决；目标仓库/插件本身属于可信输入，XML 不是
  对抗恶意同容器 root 的安全证明。后续官方 Judge 独立记录，不升级本地证据。

## M2 — PASS / aae1868556fab2a4df19b653a2bf22fecad414e6

- 复用 HookEngine 内容指纹与 file_reads；不重建 BM25。不变的工具/输出、计划改写不会
  重置无进展计数；代码变化和新测试结果会重置。默认重复 3 次 WARN，8 步无进展 REPLAN，
  最多 2 次，再持续无进展结束 stuck；全部阈值可配置，未经大样本调优。
- UUID、报告哈希、时间不作为新证据；run_tests 返回真实测试输出，失败可继续诊断。
- 背景轮询有 20 次单 job 预算；真实正在执行的 sleep job 3 次轮询均 WAITING，无误停。
  总任务 wall deadline 与终止 cleanup 属于 M4，不在此提前声称实现。
- 定向 12 passed，完整回归最终 179 passed / 9 skipped / 5 subtests；真实 Docker 1 passed，
  37.915s wall。中途仅 readability 长字符串失败，已修复且失败日志保留。
- 监测逐步记录 duration_seconds/feedback_bytes；stuck 保留 patch/预算但不 submitted/resolved。
  原始命令与轨迹：runs/v4-reliability-20261010/{commands,traces}/M2-*。

## M3 — PASS / c4d96ba474db7e98a067a02aac19c9dc21e6bda6

- V4 schema=4，单调 generation/state hash + 内容寻址 snapshot；双内容签名检测捕获期间变化。
  pre_dispatch 失败时不执行工具；post_mutation/end_step 保留配对状态。旧 V3 store 规则不动。
- 上限 64 MiB 未压缩快照、最近 4 个完整 generation；只删除无 manifest 引用的 blob。
  相同内容复用 blob。明确拒绝凭证、路径逃逸、非 regular tar、超限/磁盘写失败。
- 新进程恢复 pending call 为唯一 INTERRUPTED_UNKNOWN，不重放；保留预算/步数，重算
  evidence 状态；旧后台 job LOST_AFTER_RESTART；只清理匹配本 run label 的旧容器。
- 定向离线 28 passed；完整离线 193 passed / 18 skipped / 5 subtests。
  最终真实 Docker 9 passed（173.553s wall）：6 项 SIGKILL/独立进程恢复（pre_dispatch、
  post_edit、mid_write、active_job、坏 manifest、坏 blob），2 项真实环境拒绝副作用，
  1 项实际 Django13343 Python 3.6 编辑/语法回滚/快照恢复。
- 初次 Docker 7 failed / 2 passed，完整保留。修复：加载状态不再污染可审计 manifest；
  Python3.6 ASCII locale 遇真实 Unicode 文件名失败，V4 direct argv/helper 明确 UTF-8。
  helper 的 Path API/text/unlink 兼容变换只作用于宿主自有模板和 V4，不改目标源码。
- 原始证据：runs/v4-reliability-20261010/{commands,traces}/M3-*。
  SIGKILL 实际 process_exit=-9、resume_exit=0、旧容器移除与签名对齐写在各 fault.json。
- 约束：快照为乐观双签名一致性检查，不宣称任意恶意并发写者下的文件系统级原子快照。
  restore 仅作用于可丢弃容器 /testbed，宿主不 reset。旧 schema 不隐式迁移。

## M4 — PASS / 75b0d32814e73e262d7e925d471e3a18753a3854

- V4 前台/后台 bash 均 pipefail，保留真实退出码；grep benign 仅展示提示，SIGPIPE 141
  明示可能因 head 提前关闭；bash 永不生成 ValidationEvidence。
- RUNNING、EXITED、TIMEOUT、CANCELLED、ENV_ERROR、TOOL_ERROR 单独记录。
  后台上限 2；总 deadline 1800s（含恢复等待时间），工具/Provider 等待受剩余时限约束。
  Provider 超总时限的晚响应不再触发工具，用量未知明确记录，非计为免费调用。
- 终止时 kill 已追踪 process group 并检查没有非 zombie 活进程；finally 移除所属容器。
  Docker daemon 故障产生 cleanup_error，不能静默写已回收。容器 close 的 V4 等待为 2s。
- 离线目标 25 passed / 1 skipped；完整离线 203 passed / 25 skipped / 5 subtests。
  真实 Docker 7 passed（69.897s wall），实测 inspect：network=none、4GiB、2 CPU、
  pids=512、cap_drop=ALL、no-new-privileges。正常提交/中断/Provider 异常/总时限均清理。
- 第一次 Docker 4 failed / 2 passed，原因是新增测试错误地把 DockerClient 当 context manager；
  改为 closing(client) 并完整重跑，未放宽断言。旧 V3 管道仍返回其原协议。
- 原始记录：runs/v4-reliability-20261010/{commands,traces}/M4-*。
- 边界：Python 无法强杀请求线程，迟到响应会丢弃；网络线程可能待 SDK 自身 timeout 后退出。
  任务时限后允许有限的清理/patch 收集宽限；不是硬实时调度或多租户安全隔离。

## M5 — PASS

- V4 Trace 增加 schema/producer/代码与配置哈希、独立 validation/submission/Judge 状态；
  模型 tool_calls 为建议，tool_execution 为实际宿主执行。未知 usage/cost 保留 null。
- 一条命令实际执行 pytest/JUnit 后生成 fault_results.jsonl、metrics.csv、summary 和
  evidence_manifest；每项自己的测量分母，未测量项 NOT_MEASURED，保留失败/跳过。
- 命令：`python scripts/run_v4_reliability.py --output runs/v4-reliability-20261010/matrix-m5 --docker`。
  全量离线 210 passed / 26 skipped / 5 subtests；V4 Docker 24 passed（205.98s pytest）。
  该 suite 在 M4 HEAD + M5 未提交代码上验收，manifest 额外保存实际 source_tree_sha256。
- 汇总：false_local_pass 0/21；false_rejection 0/3；recovery_correct 12/12；
  false_stuck 0/2；cleanup_correct 5/5。混合协议与 Docker 的小样本故障矩阵，非模型成功率。
- 真 Docker 自有隐藏测试夹具：本地 PASS + 独立 Judge UNRESOLVED，Judge 信息未进 Agent。
  这是自有 fixture Judge，不冒充官方 SWE-bench；官方 Judge 留到 M6。
- Docker checkpoint 29 个样本 median 0.803214s、最大 snapshot 10371 bytes；
  progress 9 个样本 median 0.000132208s。逐次值及 backend 在 summary 中，未宣称 P99。
- 首次 telemetry 专项 3 failed / 24 passed：summary 的 tool_calls 是计数而不是列表，
  已修正仅给列表标 producer；修后 27 passed。所有初次日志继续保留。
- DEV 驱动固定三个 ID，各一次；先裁剪 dataset 到选中实例的公开字段，Agent 不读取
  gold/test patch/官方测试列表；官方 Judge 在无 Provider key 的独立进程运行。
  已检查实际 help，预览 exit 0；累计估算预算 $1，未知费用时不发后续请求。
- 无真实 Provider/HOLDOUT 调用。M6 将先用已提交代码再次完整回归，再执行真实任务。

## Pending

M6 real DEV/Judge/docs。
