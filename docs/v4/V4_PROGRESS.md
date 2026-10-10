# RepoFix V4 progress

2026-10-10（Asia/Shanghai）。隔离分支 v4，源 v3=e9f3157498d0d80197f9c6ae56df4c5bdc1273d6。
源工作区 20 个既存 tracked 删除及未跟踪任务书保留；上一轮独立实验目录保留。
不 push、不 merge、不运行或判分 HOLDOUT。真实 DEV 最后才运行，各一次、估算合计上限 $1。

## M0 — PASS (commit recorded in next milestone)

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

## Pending

M1 structured validation；M2 progress；M3 checkpoint consistency/kill-resume；
M4 lifecycle/deadline；M5 fault suite/metrics；M6 real DEV/Judge/docs。
