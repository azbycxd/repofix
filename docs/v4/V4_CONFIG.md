# V4 配置合同

默认 CLI profile 仍为 v1。明确 `--profile v4` 才启用下列新语义；v1/v3 序列化不加入 V4 字段。
配置来自 `harness/config.py::HarnessConfig.for_profile`，不是供应商或商业产品内部参数。

| V4 字段 | 默认 | 范围、代价与来源 |
|---|---:|---|
| structured_validation | true | 新 run_tests + EvidencePolicy，仅指定测试范围 |
| progress_monitor | true | 工作区/工具证据监测，不多调用 LLM |
| checkpoint_limits | true | schema4 generation，与旧 store 隔离 |
| reliability_telemetry | true | producer/状态/哈希，增加 JSONL 体积 |
| run_tests_timeout | 180 秒 | 配置/调用合法范围 1–600；工具执行时限，不是任务总时限 |
| progress_repeat_warn | 3 | 同状态同结果重复警告；首轮工程默认值 |
| progress_no_progress_steps | 8 | 连续无进展要求重规划 |
| progress_max_replans | 2 | 用尽后继续无进展则 STUCK |
| progress_job_poll_budget | 20 | 每 job 合理等待仍有有限上限 |
| checkpoint_max_bytes | 67108864 | 64 MiB 未压缩 patch + tar 上限 |
| checkpoint_keep_generations | 4 | 最近完整代，内容寻址复用 blob |
| task_deadline_seconds | 1800 | 持久化墙钟截止，恢复间隔计入；清理有宽限 |
| max_background_jobs | 2 | 同 run 后台上限，不是任务级并发 |

上述参数未经大样本调优。M6 不因 DEV 结果修改阈值。

继承配置：deepseek-flash、temperature=0、thinking disabled、reasoning_effort=none、
SDK max_retries=2、max_steps=50、单任务 max_cost_usd=0.5。本次三题累计估算预算 <=$1，
剩余预算不足以覆盖原单题 cap 加 $0.05 裕量时不启动下一题；未知费用也停止后续调用。
原有 provider_timeout_seconds=300、max_completion_tokens=8192、tool_timeout_seconds=60；
三者不替代 V4 的 1800 秒总任务 deadline。

成本公式（USD）：`(hit*0.006 + miss*0.30 + completion*1.20)/1e6`。
这是仓库已有 2026-09-28 固定价格配置，依据 `core.py` 中保存的
https://api-docs.deepseek.com/quick_start/pricing/；本轮不声称它是实时账单或价格再验证。
prompt_tokens 包含 cache-hit tokens，不能把 cache-hit 再加一次算总 token。
响应后的估算不能严格界定单次跨 cap、未返回 usage、SDK 内部重试的真实费用。

继承 V3 的机制：BM25 position-only、输出截断、reproduction-first、Hooks、read-before-edit、
apply_patch、后台 shell、Context、plan、只读工具调度、权限规则、sandbox hardening。
Dense/RRF 默认关闭，独立 Reviewer 关闭，subagents=none，verify_on_submit=false。
本轮没有单独测这些旧机制的因果增益；不是原始 Stage2 最小工具集。

输出截断沿用原配置；完整工具输出仍留容器临时文件。可信验证 stdout/stderr/XML 另外保存
宿主 artifact，避免容器销毁后只剩模型看到的摘要。工作区快照和上下文按已有脱敏策略保护。

运行入口（不运行 HOLDOUT）：

```bash
python scripts/run_agent.py --help
python scripts/run_v4_reliability.py --output runs/<全新名称> --docker
python scripts/run_v4_dev.py --output runs/<全新名称>             # 无请求预览
python scripts/run_v4_dev.py --output runs/<全新名称> --execute   # 三个固定 DEV + 独立 Judge
```

最后一条是付费真实调用，各题一次；不能用已有输出目录覆盖。恢复入口见 CLI `resume --help`。
pytest 默认不联网，真实 Docker 用例必须 `--docker`；本地 packaging 集成测试还要求
`REPOFIX_DOCKER_INTEGRATION=1`。
