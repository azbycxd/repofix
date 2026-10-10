# RepoFix V4 实测工程报告

日期：2026-10-10。范围：可靠性协议、真实 Docker 故障注入、固定 DEV 端到端回归。
没有运行、判分或读取 HOLDOUT 的问题/补丁；只读取封存 ID 列表做拒绝运行检查。

## 环境、隔离与证据层次

- 从本机 `v3 @ e9f3157498d0d80197f9c6ae56df4c5bdc1273d6` 创建独立 `v4` worktree。
  原工作区 20 个既存 tracked 删除、未跟踪任务书和旧独立实验保留；最终用 M0 哈希复核。
- WSL-native Git/Python/Docker CLI；Python 3.12.3，Docker Engine 29.4.1（Linux backend）。
  swebench 5.0.2、datasets 5.0.1、openai 3.19.2、docker SDK 7.2.0、pytest 9.1.1。
- M6 被测 Agent/Runtime 代码为 `c5d868772feb0baf760e189204c1b6948f786f98`。
  M6 只添加证据导出/报告，不根据真实 DEV 结果更改实现、Prompt、阈值或评测。
  启动时工作树含本里程碑尚未提交的报告/导出脚本，因此 manifest 的全 source_tree 哈希
  不等同于干净 c5d8687 的全树；运行前后另核对 src、run_agent 和 live driver 无 diff。
- 历史独立实验 `0a8680c743e379863cce17c43287117bfb46fc43` 存在；其历史/remote 状态
  以本机实际检查为准，没有采信任务书“未推送”的假设，也没有复制旧成绩充当本次数据。
- FakeModel/FakeEnv：协议与确定性状态转移；真实 Docker + FakeModel：容器执行和故障正确性；
  真实 DeepSeek + 官方 SWE-bench：任务级修复证据。三者不合并成模型成功率。

原始数据：`runs/v4-reliability-20261010/`（本地保留，不覆盖旧 runs）。
可审查脱敏副本：`docs/v4/evidence/`。`evidence_manifest.json` 同时记录原始/发布 SHA，
保留 checkpoint blob 的本地路径与原始哈希，不把脱敏状态文件当可恢复快照。

## 里程碑验收记录

| 阶段 | 当阶段最终离线回归 | 当阶段真实 Docker | 原始命令/日志 |
|---|---|---|---|
| M0 | 137 passed / 2 skipped / 5 subtests；V1 golden 1、V3 acceptance 4、profile 5 | daemon/image 可用性，不算集成通过 | commands/M0-* |
| M1 | 171 passed / 8 skipped / 5 subtests；定向 46 passed | 6 passed | commands/M1-* |
| M2 | 179 passed / 9 skipped / 5 subtests | 1 passed，真实 job 等待 | commands/M2-* |
| M3 | 193 passed / 18 skipped / 5 subtests | 9 passed，含六次 SIGKILL、两次拒绝风险操作、Python3.6 | commands/M3-* |
| M4 | 203 passed / 25 skipped / 5 subtests | 7 passed | commands/M4-* |
| M5 | 210 passed / 26 skipped / 5 subtests | 24 passed | matrix-m5/commands；真实 JUnit |

这些是阶段性重跑，**不把各阶段总数相加当独立样本数**。精确 argv、退出码、开始/结束时间
在各 command.json；每阶段代码先验收再提交，因此阶段验收记录同时标识 parent HEAD，
M5 一键套件额外记录被测 source_tree_sha256。

## 真实缺陷与失败日志（未删除）

1. M1 首次真实本地 pytest 生成未跟踪 `__pycache__`，误触发工作区变化；只排除未跟踪
   Python/pytest cache，跟踪文件仍绑定。日志 `commands/M1-validation-offline/`。
2. M2 完整回归发现新增源码超出 readability 行长；修复排版，未删验收。
   `commands/M2-full-regression/` 与 `M2-full-regression-readable/`。
3. M3 首次 Docker 7 failed / 2 passed：manifest 原始 dict 被 Budget 对象污染，
   以及真实 Python3.6 ASCII locale 无法读取 Django Unicode 文件名。
   修正 deepcopy 和 V4 helper UTF-8，完整再验 9 passed。
   `commands/M3-kill-resume-docker/` 与 `...-fixed/`。
4. M4 首次 Docker 4 failed / 2 passed：测试误把 DockerClient 当 context manager。
   改用 closing，不改生产判据；最终 7 passed。`commands/M4-shell-docker*`。
5. M5 首次专项 3 failed / 24 passed：trace 把 summary 中整数 tool_calls 当列表。
   仅列表才加 producer，修后 27 passed；原日志 `commands/M5-telemetry-offline*`。

以上修复均在最终真实 DEV 前完成。没有因为 DEV/Judge 成绩重跑或调参。

## 可靠性矩阵与口径

M5 实际 JUnit 产生：误放行 0/21、误拦截 0/3、恢复/拒绝风险操作检查 12/12、
正常进展误终止 0/2、清理检查 5/5。协议和 Docker 混合分母可逐行查看 backend，
不是 12 次真实崩溃恢复；其中独立进程 SIGKILL 的六个故障点是单独 6/6。
M6 的固定重复 grep 脚本实测第 25 步 STUCK（上限 50），反馈 498 bytes，
累计监测 0.001766343s；该条是 FakeModel 协议样本，不是修题效果数字。

| 注入场景 | 验收依据 | 原始测试 |
|---|---|---|
| tee 掩盖失败、打印成功、子 Agent PASS | 不能创建可信 PASS | test_v4_validation；test_v4_shell_docker |
| 无测试、全跳过、超时、未知退出码 | 非 PASS；真实通过必须是正向 PASS | test_v4_validation_docker；test_v4_run_tests |
| PASS 后编辑 | STALE | test_v4_validation |
| 三次拦截后候选提交 | forced/UNVERIFIED，不是 Judge resolved | test_v4_validation |
| 重复 grep、只改计划文字 | 有限 WARN/REPLAN/STUCK | test_v4_progress |
| 改后重测、新文件/版本、真实运行 job | 不误停；轮询仍有预算 | test_v4_progress；test_v4_progress_docker |
| pre_dispatch/post_edit/mid_write/active_job/坏 manifest/坏 blob | 真实 -9、新进程 0、配对恢复、未知调用不重放 | test_v4_resume_docker |
| ENOSPC/超限/恶意 tar/敏感快照 | fail closed，风险工具不执行 | test_v4_checkpoint；test_v4_resume_docker |
| 本地 PASS + 自有隐藏测试失败 | local PASS 与独立 UNRESOLVED 同时正确记录 | test_v4_reliability_docker |
| 正常/取消/Provider 异常/总截止 | 追踪进程组停止、容器清理；故障有事件 | test_v4_shell_docker |

指标由脚本读取真实 JUnit，而不是手写通过标记；缺失测量值为 NOT_MEASURED。
解析器自己的 XML fixture 测试不作为真实执行样本。正向、负向、恢复、清理各自分母独立。

## 开销与资源

M5 真实 Docker：checkpoint 29 个样本 median 0.803214s、最大 snapshot 10371 bytes；
progress 9 个样本 median 0.000132208s。只是当前小夹具/WSL 文件系统样本，不声称通用 P99。
逐次值保存在原 summary.overheads.measurements；真实 DEV 的大仓库开销另列，不用夹具代替。

Docker inspect 实际确认：network=none、4GiB、2 CPU、pids=512、cap_drop=ALL、
no-new-privileges。默认 root；构建期联网和依赖安装只针对可信仓库。
任务 deadline 到期后有有限 cleanup/patch 收集宽限；SDK 线程的迟到响应被丢弃，
但线程可能等其自身 timeout 才退出，不宣称强实时取消。

## 最终 M6 数据

### 最终回归

| 实际命令 | 结果 | wall time |
|---|---|---:|
| `python -m pytest -q`（加 JUnit 输出） | 210 passed / 26 skipped / 5 subtests | 165.991s |
| 任务书列出的七个 V4 专项测试文件 | 78 passed | 159.709s |
| `python -m pytest -q --docker -m docker` | 25 passed / 1 skipped / 210 deselected | 220.027s |
| `REPOFIX_DOCKER_INTEGRATION=1 ... --docker tests/test_local_sandbox_integration.py` | 1 passed | 41.197s |

唯一额外开关保护的 Docker 用例已显式补跑，所以 26 个不同 Docker 用例实际全部通过；
仍保留前一条命令的真实 skipped，不篡改原日志。78 项专项是全量回归的子集，不重复累加。
M6 故障指标仍为误放行 0/21、误拦截 0/3、恢复/拒绝风险检查 12/12、误 STUCK 0/2、清理 5/5。

### 真实 DeepSeek 与官方 Judge

命令与完整配置在 `evidence/commands/M6-live-dev/command.json`、
`evidence/dev-final/manifest.json`。模型 deepseek-flash，temperature=0、thinking disabled，
profile=v4，固定三个已知 DEV 各一次。没有额外 smoke、没有重跑、没有 HOLDOUT。

数据集 `SWE-bench/SWE-bench_Verified` / `test`；官方 swebench 5.0.2，
run_id=`dev-final-official`，max_workers=1，三个 ID 显式传给 `--instance_ids`。
官方 aggregate 报告：`evidence/dev-final/judge/deepseek-flash.dev-final-official.json`；
每题 `report.json`、`run_instance.log`、完整 `test_output.txt` 在 `evidence/dev-final/judge-raw/`。

| Task | Official Judge | Terminal | Accepted submit | Local validation | Steps | Responses | Tool calls | Prompt | Cache hit | Completion | Estimated USD | Agent wall |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| django__django-16429 | RESOLVED | no_tool_call | false | INCONCLUSIVE | 18 | 18 | 17 | 96447 | 89088 | 2122 | 0.005288628 | 348.755s |
| django__django-15277 | RESOLVED | no_tool_call | false | INCONCLUSIVE | 20 | 20 | 23 | 117111 | 109824 | 2962 | 0.006399444 | 475.125s |
| django__django-13343 | RESOLVED | no_tool_call | false | INCONCLUSIVE | 25 | 25 | 24 | 148630 | 140928 | 3511 | 0.007369368 | 387.953s |

官方结果 **3 resolved / 3 judged**，NOT_JUDGED=0；仅是固定 DEV 端到端回归。
63 steps、63 个已返回的逻辑 Provider 请求、64 tool calls；HTTP/SDK 内部重试次数未采集。
prompt=362188（其中 cache hit=339840），completion=8595，总 token=370783。
总估算 USD **0.019057440**，低于 $1 预算；按旧固定配置计价，不是真实账单。

Agent 方法报告 wall 合计 1211.833s；驱动端 Agent 阶段 1265.885s（包括初始化/容器等），
官方 Harness wall 75.538s；整个 live 驱动命令 1347.549s（约 22.46 分钟）。
总体回归与 Docker 测试时间另列，不混入 Agent 性能数字。

三份候选 patch 均非空，只有生产文件修改，filtered_test_paths=[]，本轮过滤测试策略没有
改变这些 patch 的内容。实际修改文件分别为 `django/utils/timesince.py`、
`django/db/models/fields/__init__.py`、`django/db/models/fields/files.py`。
full/evaluation patch 和 JSONL 在 `evidence/dev-final/` 及 `evidence/dev-final/traces/<id>/`。
search_code=0、输出截断=0；不能据此声称检索/截断带来了本轮效果。

### 不能被 3/3 掩盖的真实问题

三题都尝试过 submit（分别 2、3、3 次），但缺少支持的 pytest，宿主一直没有可信本地 PASS。
Agent 没有走到第四次 forced submit，最终因不再调用工具而 `no_tool_call`，不是 interrupted。
runner 仍保存候选 patch 并交给独立 Judge；不要把这描述成三个顺畅完成的交互流程。

- 16429：第 7、16 步验证为环境缺失；第 13 步 pip 安装请求被规则拒绝；第 17 步 submit
  被拦截；第 18 步 no_tool_call。修复代码有效，但本地验证/提交体验没有顺利闭环。
- 15277：第 17、19 步 submit 被拦截；第 20 步 no_tool_call。相同环境限制，非业务修复失败。
- 13343：第 17 步原生 Django 命令显示 58 tests / OK，但这只是 bash 观察，不可升级为
  结构化本地 PASS；第 21 步 run_tests 仍缺 pytest，第 24 步 submit 仍被拦截。
  第 25 步触发 1 次 REPLAN，同时 no_tool_call 终止，反馈没有被下一次模型请求消费。
  因而这次不能宣称重规划改善了真实任务结果。

本次没有官方 UNRESOLVED；以上是 **验证 runner 环境支持与提交可用性** 的真实缺口，
不是把它们删掉后才得到 3/3。普通 Django tests 的文本输出不被偷换成本地信任证据。
未根据这些结果改系统；后续改造需要独立任务和新实验。

### 大仓库实际开销

| Task | Checkpoint samples | Checkpoint sum / median | Max snapshot bytes | Progress sum | Decision-model wait sum |
|---|---:|---:|---:|---:|---:|
| 16429 | 50 | 111.385s / 2.324s | 10656 | 0.005153s | 153.494s |
| 15277 | 59 | 155.259s / 2.754s | 10929 | 0.005925s | 157.306s |
| 13343 | 67 | 205.354s / 3.111s | 11163 | 0.007733s | 24.979s |

176 次 checkpoint 累计约 **471.998s**，不是可以忽略的工程代价；包括签名、捕获、持久化
及回收的组合开销，本轮没有细分根因或优化频次。snapshot bytes 是单次捕获量，不是
累计磁盘占用；内容寻址仍会复用 blob。真实 DEV 的恢复开销不能用小夹具 median 代替。
前两题各有一次约 135 秒的模型等待；没有 HTTP 层重试日志，不能断言是模型推理时间。
tool duration 只对返回了该测量字段的调用统计（16/17、22/23、23/24），缺测不填 0。
计时区间可能含嵌套/并行，不能把所有分项相加冒充严格 wall-time 分解。

## 审计与交付

原 v3 HEAD、原 dirty status、冻结文件与原 dev_artifacts、上一轮实验 manifest 均与 M0 哈希核对。
运行前后 `src`、`run_agent.py`、live driver 与 c5d8687 无 diff。结束后 `docker ps -a --filter name=repofix`
为空，官方 report 的 unstopped_containers 为空；已有三个 SWE-bench 镜像保留，不执行清理旧缓存。

导出按完整原始记录生成脱敏副本，不摘要化 trajectory；已知 key 逐字节检查，Bearer/JWT/本机路径
脱敏，真实 Agent 轨迹检查不含 HOLDOUT ID。范围与实际检查结果见 `audit.json`。
这不是证明任意未知字符串都不是 secret 的通用 DLP 认证。
最终 Git 提交不含 runs、.env、缓存和 snapshot blob；只提交独立新文档证据目录中的审查材料。

## 尚未验证与不能外推

- pytest 之外的结构化测试 runner 尚未支持；官方 Django 镜像缺少 pytest 是真实环境限制，
  没有偷偷安装/改镜像或用文本 PASS 降低标准。
- 本地通过不等于需求覆盖，fixture 的隐藏失败专门展示这一点。
- 不是恶意仓库、多租户安全服务；权限前缀只是意图/审计提示。
- 快照双签名不是恶意并发写入下的 filesystem transaction；未测断电、跨主机恢复。
- SDK 内部重试和无 usage 错误的真实账单未知，只有返回 usage 的估算。
- 三道题已经参与开发，不是 sealed benchmark，不证明泛化；HOLDOUT 本轮运行数 0。
- 没有单独证明子 Agent、Context 或检索带来效果收益，没有进行旧版本成绩竞争。
