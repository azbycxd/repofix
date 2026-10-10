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

## M1 — PASS

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
- 边界：本地测试范围不代表 issue 已解决；目标仓库/插件本身属于可信输入，XML 不是
  对抗恶意同容器 root 的安全证明。后续官方 Judge 独立记录，不升级本地证据。

## Pending

M2 progress；M3 checkpoint consistency/kill-resume；
M4 lifecycle/deadline；M5 fault suite/metrics；M6 real DEV/Judge/docs。
