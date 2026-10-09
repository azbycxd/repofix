# RepoFix V3 真实实验报告

日期：2026-10-09，Asia/Shanghai。性质：已知 DEV 回归／探索性 V1/V3 对照，不是新 Holdout benchmark。

## 结论先行

实际执行 3 道任务 × 2 个 profile × 1 次，共 6 次真实 DeepSeek + Docker。官方 SWE-bench 独立判分：V1 **3/3**，V3 **3/3**；6 次均 submitted，Judge 失败、运行终止错误、未判分均为 0。没有最终失败题，但存在真实工具错误和验证不充分的负面证据，详见案例。没有重跑任何模型任务或人工修补 Agent patch。

V3 多用 14 步（+31.82%）、112,817 prompt tokens（+50.45%）、估算费用多 $0.004871220（+39.41%），没有多修好题。不能据此声称 V3 效果提升；也不能据此证明 V3 在未知长任务上无用。

## 1. 环境、版本、计划和安全边界

任务指定的长文件名在根目录不存在；实际读取并执行根目录《真实评测任务书.md》，未修改这份任务书或结果模板。执行入口来自 `v3`，源仓库 HEAD 为 `e9f3157498d0d80197f9c6ae56df4c5bdc1273d6`；主分支仍为 `3fe3c0e25a8aa72b2d9e73bee4d995228b9300ac`。

隔离分支：`codex/interview-real-20261009`。WSL 工作目录：`/mnt/d/workspace/repo-agent-project/repofix/.cache/interview-real-20261009/repo`。6 次运行首行记录的实际 Git SHA 全部为 `7227920d3b2e50b43a7295b16cb4bf1eeb340301`；相对源版本，只增加评测／传输／报告辅助脚本，Agent 业务实现、Prompt、Tools、tests 和冻结配置未改。报告生成脚本版本：`80af1e1fe95cf762699baffd598dce95406bd250`。后续文档提交不改变实验版本。

WSL `/home/jiusi/venvs/repofix/bin/python`，Python 3.12.3；swebench 5.0.2、datasets 5.0.1、openai 3.19.2、docker SDK 7.2.0、pytest 9.1.1。Docker Desktop 4.71.0 / Engine 29.4.1 / API 1.54 / linux-amd64。Git、Python、Docker 均实际在 WSL 执行；Windows curl 仅用于经既有本地代理下载公开镜像字节。

计划在首次模型调用前冻结，时间 `2026-10-09T12:00:03.706563+00:00`。按 tasks.txt 原顺序取前 3 道：16429、15277、13343；为约 3 小时／$3 预算内优先保证独立判分，没有按结果筛题。完整 ID 在下表。每题先 V1 后 V3，各一次。其余 DEV 未运行；HOLDOUT 不运行、不判分、不读取内容；只读 ID 做拒绝运行检查、旧文件名／哈希做保护审计。

API key 仅从本机 gitignored .env / 环境变量读取，报告只记录存在性；不进入 Docker、Judge、命令参数或提交。没有把 gold patch、官方 test patch 或隐藏评测结果送入 Agent；Judge 结束后不再让模型处理判分结果。容器访问公开基线仓库，runtime network=none。Codex 负责评测辅助代码、基础设施排障、执行和证据整理；修复 patch 来自被测 DeepSeek，不是 Codex 代修。

### 配置与仍然存在的差异

共同：deepseek-flash，api.deepseek.com，temperature=0，thinking=disabled，最多 50 步，单任务估算 $0.5 上限（响应后计费，不是请求前硬拦截），provider timeout=300s，SDK max_retries=2，max_completion_tokens=8192，BM25，12,000 字符头尾截断，reproduction-first，production-only evaluation patch。Dense/RRF、Reviewer、subagents 均关闭。未设置随机 seed。

两组初始 bugfix system/user prompt 相同；V3 原生工具集合、read-before-edit、Hooks、Checkpoint、Context、权限检查、后台 shell 等不同。V3 readonly 并行开启但这轮没有观测到并行批次；V3 默认 sandbox hardening 使用 2 CPU / 4GB / drop capabilities，V1 无同样资源限制。因此不是“只改变 Context”的单变量实验。V1 固定先运行，Provider cache／时段顺序可能影响成本和耗时，temperature=0 也不保证重复结果完全一致。

### 镜像基线身份

使用官方 `swebench/sweb.eval.x86_64.django_1776_django-<ID>:latest`。镜像 HEAD 为额外的 SWE-bench commit，与 dataset base SHA 不同，但逐镜像读取本地 Git 对象确认 source tree 完全一致、diff 为空、运行前工作树干净。不是只凭 tag 或 SHA 字符串猜测。

| Task | Dataset base SHA | Image HEAD | Equal source tree SHA |
| --- | --- | --- | --- |
| django__django-13343 | ece18207cbb64dd89014e279ac636a6c9829828e | e5321912f54a757ae2b85149b63f068419db7858 | 0aeee2b07eb0eaab4bce5f93f4db77f990c56802 |
| django__django-15277 | 30613d6a748fce18919ff8b0da166d9fda2ed9bc | f8b0c824706d5143b52a3a82bbda47364a25c004 | a3ddfcff5c655e490e6c9d87cf53fa8e15b899a2 |
| django__django-16429 | 6c86495bcee22eac19d7fb040b2988b830707cbd | 38a838d157a5ced78f19ad289bdd2d93226fc4df | 50c727d80cf933acfdbd167d3d424081e06b0eef |

16429/15277 testbed Python=3.9.20；13343=3.6.13。解释器均在 `/opt/miniconda3/envs/testbed/bin/python`，Django 导入 `/testbed/django/__init__.py`。环境证据见 `runs/interview-real-20261009/docker_sanity.json` 和 `image_local_baselines.json`。前者外部 GitHub tree 查询超时，后者以本地对象确认，两个原始记录均保留。

## 2. 四层证据，不混用

| 层 | 本次实际执行 | 结果与边界 |
| --- | --- | --- |
| A 离线 | pytest 全套；V1 golden 单独复测 | 132 passed、2 skipped、5 subtests passed；golden 1 passed，已包含在全套，不重复计数 |
| B 真 Docker | `REPOFIX_DOCKER_INTEGRATION=1 ... -m docker --docker` | 首次 2 failed：镜像未就绪、拉取 EOF；镜像就绪后 2 passed、132 deselected，两个结果都保留 |
| B 补充机制 | 真实容器大文件／Hooks／artifact／snapshot 四项 | 4/4 通过，使用可控状态及 FakeModel，不计真实模型效果 |
| C 真 DeepSeek + Docker | 6 个 DEV × profile run | 6 submitted、0 fatal、6 非空 full/evaluation patch；102 次 SDK 层请求，底层 HTTP 重试次数未单独计量 |
| D 官方独立 Judge | v1/v3 两个批次，各 3 个 prediction | 合计 6 resolved、0 unresolved、0 empty、0 error、0 NOT_JUDGED |

pytest 原有两个真实 Docker 测试分别覆盖 local sandbox editable import/network=none 和后台 job 跨短等待存活、最终结束。第二次 Docker pytest 内部计时 62.87s，含进程清理 wall=152.097s；两种口径不混写。全套离线 pytest 内部 10.62s，命令 wall=19.977s。

补充机制结果 `runs/interview-real-20261009/docker-mechanisms/results.json`：336,010 字符文件真实传输／连续编辑／语法回滚通过；实际 unittest exit 1 不标 harmless，阻挡 submit 一次；36,026 字符 context artifact 在容器读回完全一致（mask-only，未调用摘要模型）；CheckpointStore 恢复 tracked + untracked，并把 pending call 回填 interrupted、不重执行。**尚未覆盖真实模型被杀后完整 CLI 恢复流程**，不能把这项局部测试写成全量 Resume 已验证。

## 3. 完整真实运行数据

| Task | Profile | Official Judge | Terminal / submitted | Steps / Provider / Tools | Prompt | Cache hit | Completion | Est. USD | Loop / process s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| django__django-16429 | v1 | RESOLVED | submitted / True | 10 / 10 / 10 | 32357 | 30588 | 1437 | 0.002438628 | 23.252 / 40.451 |
| django__django-16429 | v3 | RESOLVED | submitted / True | 11 / 11 / 11 | 41678 | 36864 | 1315 | 0.003243384 | 35.435 / 59.303 |
| django__django-15277 | v1 | RESOLVED | submitted / True | 9 / 9 / 12 | 25770 | 23424 | 1213 | 0.002299944 | 25.211 / 42.290 |
| django__django-15277 | v3 | RESOLVED | submitted / True | 12 / 12 / 15 | 45886 | 41728 | 1510 | 0.003309768 | 51.398 / 75.055 |
| django__django-13343 | v1 | RESOLVED | submitted / True | 25 / 25 / 27 | 165505 | 158080 | 3705 | 0.007621980 | 188.454 / 205.244 |
| django__django-13343 | v3 | RESOLVED | submitted / True | 35 / 35 / 36 | 248885 | 238720 | 5164 | 0.010678620 | 278.872 / 303.913 |

所有数字来自 `.result.json`／JSONL／官方 report，CSV 由程序导出，并经独立 CSV 读入审计逐单元格核对；空值不填 0。首次文档暂存检查把 CSV 的默认 CRLF 判为 trailing whitespace，导出器改用 LF 后重新检查，数值和原始记录均未改变。完整字段见 `results.csv`，每行记录 profile、repeat、配置哈希、SHA、run_id、real_provider/real_docker、Judge、token、机制事件及证据路径。没有删去失败轨迹；本次没有最终失败 run。

### 同题矩阵

| Task | V1 | V3 | Steps V1/V3 | Est. USD V1/V3 |
| --- | --- | --- | --- | --- |
| django__django-16429 | RESOLVED | RESOLVED | 10 / 11 | 0.002438628 / 0.003243384 |
| django__django-15277 | RESOLVED | RESOLVED | 9 / 12 | 0.002299944 / 0.003309768 |
| django__django-13343 | RESOLVED | RESOLVED | 25 / 35 | 0.007621980 / 0.010678620 |

V3 独胜=0，V1 独胜=0，两者都失败=0，两者都成功=3。不是 6 道独立题，而是 3 道已知题的两种配置各一次。不能计算或宣称泛化成功率，未报告有意义的 P95 或显著性。

### 汇总、时间和估算费用

| Group | Resolved / judged / planned | Submitted | Steps / average | Prompt / cache / completion | Est. USD | Agent loop / process s | Judge process s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | 3 / 3 / 3 | 3 / 3 | 44 / 14.67 | 223632 / 212092 / 6355 | 0.012360552 | 236.918 / 287.985 | 73.667 |
| v3 | 3 / 3 / 3 | 3 / 3 | 58 / 19.33 | 336449 / 317312 / 7989 | 0.017231772 | 365.705 / 438.271 | 174.759 |

合计 prompt=560,081，completion=14,344，total=574,425 tokens；cache hit=529,404 是 prompt 的子集，不额外加一次。工具调用=111。

冻结价格：cache hit input $0.006/M、miss input $0.30/M、output $1.20/M，沿用仓库配置及 pricing_source，不声称核对了当天账单。公式 `(prompt-cache)*0.30/1e6 + cache*0.006/1e6 + completion*1.20/1e6`。合计 **$0.029592324 估算 USD**，不是实际支付凭证。

Agent loop 合计 602.623s；完整 Agent 进程（含容器/index/setup/cleanup）726.256s；官方 Judge 两批合计 248.426s；Agent 进程 + Judge=974.682s。计划冻结到最后 Judge 结束的实验窗口=2595.757s，包含基础设施准备/检查，约 43.26 分钟；不包含更早源码审阅或后续写文档时间。

Judge 仅记录批次 wall，单题 `judge_wall_seconds=null`，不均摊伪造。V1 13343 首步总时长 136.09s，原始 V1 未拆 model/tool 时间，不能全归因网络或模型。V3 与 V1 Judge 时差不视为代码性能差异。

## 4. 案例：最终修复与过程中真实问题

### 16429：修好，但 read-before-edit 增加了一次摩擦

公开 issue 是 timesince 在 aware/naive datetime 间计算失败。V1 step 2 复现异常，step 3 str_replace 修改 `django/utils/timesince.py`，datetime pivot 保留 microsecond/tzinfo，复验后 step 10 submit。V3 step 2 复现，step 3 的 str_replace 因未有 native view 记录被拒绝；此前通过 bash 读过源码，所以不能描述为“Agent 完全没读文件”。step 4 view、step 5 编辑成功，step 11 submit。两组官方 resolved。

这是工具 guard 生效而非 hook deny：V3 hook_blocks=0；本例多一步、多 prompt／费用，没有证据说明 guard 改善最终 patch。相同生产补丁并不代表整个运行轨迹或资源用量相同。

### 15277：自动复现 telemetry 不是权威事实

issue 是 CharField(max_length=None) 装上 MaxLengthValidator，clean 时 TypeError。V1 step 2 的脚本捕获 TypeError 后 exit 0，原始输出明确打印了问题；step 3 加 `self.max_length is not None` 条件，step 4 同脚本输出 `validators: [] / clean OK`。自动 PRE_FIX_REPRODUCED 和 REPRO_FLIPPED 却为 false。V3 step 4 复现 exit 1，step 5 改动，step 6 同判据通过，自动字段为 true。两组官方 resolved。报告保留原字段，不把“false”误写为没复现，也不改原始遥测来凑一致。

### 13343：官方修好，但原生工具旧 Python 兼容失败与测试状态掩盖

issue 是 FileField 的 callable storage 经 deconstruct 丢失 callable。V1 step 4 复现断言失败，step 7/8 编辑、step 9 同脚本通过，step 25 submit。V3 step 5 复现；step 3 view、step 7 str_replace、step 9 apply_patch 均遇到 `AttributeError: 'PosixPath' object has no attribute 'is_relative_to'`，镜像为 Python 3.6。step 8 另有 patch markers 格式错误。step 10 Agent 自己改用 bash Python 写文件，step 12 调整实现，step 13 原复现通过，最终 step 35 submit。没有 Codex 手工补 patch，也没有修 Harness 后重跑。

V3 step 28 的联合测试通过 `2>&1 | tail -6` 执行，shell exit=0，但输出是 1,081 tests、FAILED(failures=205, errors=223, skipped=11)。step 29 暂存补丁检查未修基线也有大量失败；step 32 migrations 单独 560 tests OK；step 33 分别跑 suite 时 file_storage 仍 errors=2，model_fields/migrations OK。并非完整测试全绿。V1 同样出现 broad tests 失败和管道尾命令 exit 0；部分有 locale/network 环境原因，不能笼统归因 patch，也不能据此豁免验证责任。

Hooks 读取到的是进程退出状态，不能保证识别管道输出中的所有失败，V3 本轮 hook_blocks=0。独立 SWE-bench 最终两组都 resolved；这是“任务判分通过”与“工具／广泛验证有缺陷”并存的案例，不应包装成毫无问题的成功。13343 两组都在已有 `tests/file_storage/tests.py` 增加回归测试；完整 diff 保留，官方 prediction 过滤测试文件，未删除／弱化已有断言。这轮过滤不是按 Judge 结果临时加规则。

## 5. 机制真正触发到哪一层

| 机制 | A/B 证据 | C 类实际观察 | 可说与不能说 |
| --- | --- | --- | --- |
| Hooks | 真 Docker unittest 失败能阻 submit | 有 Pre/Post/Stop 事件，blocks=0 | 框架接入，不能说本轮拦住了真实坏补丁 |
| Checkpoint | 真 Docker snapshot/restore + pending fill | V3 11/12/35 个 step checkpoint 文件 | 文件数不是 save 次数；未做真实模型完整中断续跑 |
| Context | 真 Docker mask artifact 可读回 | compactions=0 | 未触发，不能评价摘要／压缩收益 |
| 输出截断 | 代码/既有离线测试有覆盖 | truncations=0 | 不能解释本轮 token 差异 |
| Read-before-edit | 既有离线测试 | 16429 拒绝一次，13343 runtime 兼容报错 | 有摩擦与兼容代价，不等同正确性提升 |
| BM25 | 两组均构建索引 | V1 search=1；V3 search=0 | 不能说 V3 主动检索带来收益 |
| readonly parallel | 已实现 | 观测到的 parallel batches=0 | 不声称本轮获得并发收益 |
| Subagent | 代码存在、默认 none | calls=0 | 没有测试多 Agent 效果 |
| Dense / Reviewer | 关闭 | 无调用 | 不是此次对照变量 |

| Task / profile | Auto pre-repro | Auto flip | First production edit | Search | Output truncations |
| --- | --- | --- | --- | --- | --- |
| django__django-16429 / v1 | True | True | 3 | 0 | 0 |
| django__django-16429 / v3 | True | True | 5 | 0 | 0 |
| django__django-15277 / v1 | False | False | 3 | 0 | 0 |
| django__django-15277 / v3 | True | True | 5 | 0 | 0 |
| django__django-13343 / v1 | True | True | 7 | 1 | 0 |
| django__django-13343 / v3 | True | True | 10 | 0 | 0 |

## 6. 失败、阻塞及恢复记录

开始 Docker Desktop 未启动，启动本机已安装服务后可用。首次 Docker 集成测试失败、Docker Hub 拉取 EOF；WSL skopeo 公开镜像复制因 auth.docker.io 路由／认证网络超时失败。使用现有 Windows 本地代理下载官方 manifest/config/layers，逐项 SHA256 校验，再以 WSL docker load 导入。没有更换为未知镜像或修改全局代理/daemon 配置。

首版导入辅助脚本错误地要求 Docker image ID 等于 OCI config digest；Docker 29 containerd 导入后返回转换后的 manifest ID。保留该失败，评测辅助脚本改为检查 rootfs diff_ids、runtime Config、架构/OS，后续确认成功。首次基线检查要求 HEAD SHA 字符相等也不成立；后续本地 Git tree 核对三题均相同。原始失败日志、未完成的外部查询均保留，不写成 Agent 回归。

这些恢复只涉及环境／评测脚本，在首次模型调用之前完成必要导入及检查，事后追加的本地 Git 对象检查为只读。没有改业务实现或通过重跑抹掉失败。BLOCKERS.md 区分已解除阻塞与待修产品问题。

## 7. 实际命令和证据定位

以下为保存的原始 argv；cwd 见各 command.json，Docker pytest 另设置 `REPOFIX_DOCKER_INTEGRATION=1`，模型进程单独加载 key 环境变量，未放 argv。其他四次 Agent 的完整命令也在附表，只有 profile/task/run_id/output-dir 不同。脚本返回码之外还核对 result.status，未发现 runner_error。

```bash
/home/jiusi/venvs/repofix/bin/python -m pytest -q
```

```bash
/home/jiusi/venvs/repofix/bin/python -m pytest -q tests/test_v1_golden.py
```

```bash
/home/jiusi/venvs/repofix/bin/python -m pytest -q -m docker --docker
```

```bash
/home/jiusi/venvs/repofix/bin/python scripts/run_agent.py --help
```

```bash
/home/jiusi/venvs/repofix/bin/python -m swebench.harness.run_evaluation --help
```

```bash
/home/jiusi/venvs/repofix/bin/python scripts/run_agent.py --profile v1 --instance-id django__django-16429 --run-id interview-real-20261009-v1-django__django-16429 --output-dir /mnt/d/workspace/repo-agent-project/repofix/.cache/interview-real-20261009/repo/runs/interview-real-20261009/v1/django__django-16429
```

```bash
/home/jiusi/venvs/repofix/bin/python scripts/run_agent.py --profile v3 --instance-id django__django-16429 --run-id interview-real-20261009-v3-django__django-16429 --output-dir /mnt/d/workspace/repo-agent-project/repofix/.cache/interview-real-20261009/repo/runs/interview-real-20261009/v3/django__django-16429
```

```bash
/home/jiusi/venvs/repofix/bin/python -m swebench.harness.run_evaluation --dataset_name SWE-bench/SWE-bench_Verified --split test --predictions_path /mnt/d/workspace/repo-agent-project/repofix/.cache/interview-real-20261009/repo/runs/interview-real-20261009/judge/v1/predictions.json --run_id interview-real-20261009-v1 --max_workers 1 --timeout 900 --report_dir /mnt/d/workspace/repo-agent-project/repofix/.cache/interview-real-20261009/repo/runs/interview-real-20261009/judge/v1 --instance_ids django__django-16429 django__django-15277 django__django-13343
```

```bash
/home/jiusi/venvs/repofix/bin/python -m swebench.harness.run_evaluation --dataset_name SWE-bench/SWE-bench_Verified --split test --predictions_path /mnt/d/workspace/repo-agent-project/repofix/.cache/interview-real-20261009/repo/runs/interview-real-20261009/judge/v3/predictions.json --run_id interview-real-20261009-v3 --max_workers 1 --timeout 900 --report_dir /mnt/d/workspace/repo-agent-project/repofix/.cache/interview-real-20261009/repo/runs/interview-real-20261009/judge/v3 --instance_ids django__django-16429 django__django-15277 django__django-13343
```

### 六份轨迹、两种 patch、独立判分

| Task / profile | Raw trajectory | Full / evaluation patch | Official per-task report |
| --- | --- | --- | --- |
| django__django-16429 / v1 | [JSONL](../../../runs/interview-real-20261009/v1/django__django-16429/django__django-16429.jsonl) | [full](../../../runs/interview-real-20261009/v1/django__django-16429/django__django-16429.patch) / [production-only](../../../runs/interview-real-20261009/v1/django__django-16429/django__django-16429.evaluation.patch) | [report.json](../../../logs/run_evaluation/interview-real-20261009-v1/deepseek-flash/django__django-16429/report.json) |
| django__django-16429 / v3 | [JSONL](../../../runs/interview-real-20261009/v3/django__django-16429/django__django-16429/trajectory.jsonl) | [full](../../../runs/interview-real-20261009/v3/django__django-16429/django__django-16429.patch) / [production-only](../../../runs/interview-real-20261009/v3/django__django-16429/django__django-16429.evaluation.patch) | [report.json](../../../logs/run_evaluation/interview-real-20261009-v3/deepseek-flash/django__django-16429/report.json) |
| django__django-15277 / v1 | [JSONL](../../../runs/interview-real-20261009/v1/django__django-15277/django__django-15277.jsonl) | [full](../../../runs/interview-real-20261009/v1/django__django-15277/django__django-15277.patch) / [production-only](../../../runs/interview-real-20261009/v1/django__django-15277/django__django-15277.evaluation.patch) | [report.json](../../../logs/run_evaluation/interview-real-20261009-v1/deepseek-flash/django__django-15277/report.json) |
| django__django-15277 / v3 | [JSONL](../../../runs/interview-real-20261009/v3/django__django-15277/django__django-15277/trajectory.jsonl) | [full](../../../runs/interview-real-20261009/v3/django__django-15277/django__django-15277.patch) / [production-only](../../../runs/interview-real-20261009/v3/django__django-15277/django__django-15277.evaluation.patch) | [report.json](../../../logs/run_evaluation/interview-real-20261009-v3/deepseek-flash/django__django-15277/report.json) |
| django__django-13343 / v1 | [JSONL](../../../runs/interview-real-20261009/v1/django__django-13343/django__django-13343.jsonl) | [full](../../../runs/interview-real-20261009/v1/django__django-13343/django__django-13343.patch) / [production-only](../../../runs/interview-real-20261009/v1/django__django-13343/django__django-13343.evaluation.patch) | [report.json](../../../logs/run_evaluation/interview-real-20261009-v1/deepseek-flash/django__django-13343/report.json) |
| django__django-13343 / v3 | [JSONL](../../../runs/interview-real-20261009/v3/django__django-13343/django__django-13343/trajectory.jsonl) | [full](../../../runs/interview-real-20261009/v3/django__django-13343/django__django-13343.patch) / [production-only](../../../runs/interview-real-20261009/v3/django__django-13343/django__django-13343.evaluation.patch) | [report.json](../../../logs/run_evaluation/interview-real-20261009-v3/deepseek-flash/django__django-13343/report.json) |

Judge 聚合文件分别是 `runs/interview-real-20261009/judge/v1/deepseek-flash.interview-real-20261009-v1.json` 与 v3 同名形式。每题判分目录保留 report.json、run_instance.log、test_output.txt、eval.sh、patch.diff。原始轨迹只保留 runs 内这一份，未复制到旧 dev_artifacts。模型阶段结束后才产生/读取这些判分材料。

### 全部受记录命令，包括失败

| Command label | Exit | Wall s | Raw command + stdout/stderr |
| --- | --- | --- | --- |
| agent-help | 0 | 2.179 | [command.json](../../../runs/interview-real-20261009/commands/agent-help/command.json) / [stdout](../../../runs/interview-real-20261009/commands/agent-help/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/agent-help/stderr.log) |
| agent-v1-django__django-13343 | 0 | 205.244 | [command.json](../../../runs/interview-real-20261009/commands/agent-v1-django__django-13343/command.json) / [stdout](../../../runs/interview-real-20261009/commands/agent-v1-django__django-13343/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/agent-v1-django__django-13343/stderr.log) |
| agent-v1-django__django-15277 | 0 | 42.290 | [command.json](../../../runs/interview-real-20261009/commands/agent-v1-django__django-15277/command.json) / [stdout](../../../runs/interview-real-20261009/commands/agent-v1-django__django-15277/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/agent-v1-django__django-15277/stderr.log) |
| agent-v1-django__django-16429 | 0 | 40.451 | [command.json](../../../runs/interview-real-20261009/commands/agent-v1-django__django-16429/command.json) / [stdout](../../../runs/interview-real-20261009/commands/agent-v1-django__django-16429/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/agent-v1-django__django-16429/stderr.log) |
| agent-v3-django__django-13343 | 0 | 303.913 | [command.json](../../../runs/interview-real-20261009/commands/agent-v3-django__django-13343/command.json) / [stdout](../../../runs/interview-real-20261009/commands/agent-v3-django__django-13343/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/agent-v3-django__django-13343/stderr.log) |
| agent-v3-django__django-15277 | 0 | 75.055 | [command.json](../../../runs/interview-real-20261009/commands/agent-v3-django__django-15277/command.json) / [stdout](../../../runs/interview-real-20261009/commands/agent-v3-django__django-15277/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/agent-v3-django__django-15277/stderr.log) |
| agent-v3-django__django-16429 | 0 | 59.303 | [command.json](../../../runs/interview-real-20261009/commands/agent-v3-django__django-16429/command.json) / [stdout](../../../runs/interview-real-20261009/commands/agent-v3-django__django-16429/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/agent-v3-django__django-16429/stderr.log) |
| docker-mechanisms | 0 | 29.319 | [command.json](../../../runs/interview-real-20261009/commands/docker-mechanisms/command.json) / [stdout](../../../runs/interview-real-20261009/commands/docker-mechanisms/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/docker-mechanisms/stderr.log) |
| docker-official-baselines | 0 | 35.087 | [command.json](../../../runs/interview-real-20261009/commands/docker-official-baselines/command.json) / [stdout](../../../runs/interview-real-20261009/commands/docker-official-baselines/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/docker-official-baselines/stderr.log) |
| docker-sanity-tree-verified | 1 | 43.014 | [command.json](../../../runs/interview-real-20261009/commands/docker-sanity-tree-verified/command.json) / [stdout](../../../runs/interview-real-20261009/commands/docker-sanity-tree-verified/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/docker-sanity-tree-verified/stderr.log) |
| environment | 0 | 0.775 | [command.json](../../../runs/interview-real-20261009/commands/environment/command.json) / [stdout](../../../runs/interview-real-20261009/commands/environment/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/environment/stderr.log) |
| final-diff-check | 0 | 0.270 | [command.json](../../../runs/interview-real-20261009/commands/final-diff-check/command.json) / [stdout](../../../runs/interview-real-20261009/commands/final-diff-check/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/final-diff-check/stderr.log) |
| final-docker-disk | 0 | 0.323 | [command.json](../../../runs/interview-real-20261009/commands/final-docker-disk/command.json) / [stdout](../../../runs/interview-real-20261009/commands/final-docker-disk/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/final-docker-disk/stderr.log) |
| final-experiment-containers | 0 | 0.037 | [command.json](../../../runs/interview-real-20261009/commands/final-experiment-containers/command.json) / [stdout](../../../runs/interview-real-20261009/commands/final-experiment-containers/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/final-experiment-containers/stderr.log) |
| judge-help | 0 | 1.424 | [command.json](../../../runs/interview-real-20261009/commands/judge-help/command.json) / [stdout](../../../runs/interview-real-20261009/commands/judge-help/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/judge-help/stderr.log) |
| judge-v1 | 0 | 73.667 | [command.json](../../../runs/interview-real-20261009/commands/judge-v1/command.json) / [stdout](../../../runs/interview-real-20261009/commands/judge-v1/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/judge-v1/stderr.log) |
| judge-v3 | 0 | 174.759 | [command.json](../../../runs/interview-real-20261009/commands/judge-v3/command.json) / [stdout](../../../runs/interview-real-20261009/commands/judge-v3/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/judge-v3/stderr.log) |
| pull-django__django-13343 | 1 | 5.238 | [command.json](../../../runs/interview-real-20261009/commands/pull-django__django-13343/command.json) / [stdout](../../../runs/interview-real-20261009/commands/pull-django__django-13343/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/pull-django__django-13343/stderr.log) |
| pull-django__django-15277 | 1 | 5.194 | [command.json](../../../runs/interview-real-20261009/commands/pull-django__django-15277/command.json) / [stdout](../../../runs/interview-real-20261009/commands/pull-django__django-15277/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/pull-django__django-15277/stderr.log) |
| pull-django__django-16429 | 1 | 5.187 | [command.json](../../../runs/interview-real-20261009/commands/pull-django__django-16429/command.json) / [stdout](../../../runs/interview-real-20261009/commands/pull-django__django-16429/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/pull-django__django-16429/stderr.log) |
| pull-python | 1 | 5.188 | [command.json](../../../runs/interview-real-20261009/commands/pull-python/command.json) / [stdout](../../../runs/interview-real-20261009/commands/pull-python/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/pull-python/stderr.log) |
| pytest-docker | 1 | 8.799 | [command.json](../../../runs/interview-real-20261009/commands/pytest-docker/command.json) / [stdout](../../../runs/interview-real-20261009/commands/pytest-docker/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/pytest-docker/stderr.log) |
| pytest-docker-images-ready | 0 | 152.097 | [command.json](../../../runs/interview-real-20261009/commands/pytest-docker-images-ready/command.json) / [stdout](../../../runs/interview-real-20261009/commands/pytest-docker-images-ready/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/pytest-docker-images-ready/stderr.log) |
| pytest-final-offline | 0 | 10.201 | [command.json](../../../runs/interview-real-20261009/commands/pytest-final-offline/command.json) / [stdout](../../../runs/interview-real-20261009/commands/pytest-final-offline/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/pytest-final-offline/stderr.log) |
| pytest-offline | 0 | 19.977 | [command.json](../../../runs/interview-real-20261009/commands/pytest-offline/command.json) / [stdout](../../../runs/interview-real-20261009/commands/pytest-offline/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/pytest-offline/stderr.log) |
| pytest-v1-golden | 0 | 1.573 | [command.json](../../../runs/interview-real-20261009/commands/pytest-v1-golden/command.json) / [stdout](../../../runs/interview-real-20261009/commands/pytest-v1-golden/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/pytest-v1-golden/stderr.log) |
| registry-copy-python | 1 | 31.028 | [command.json](../../../runs/interview-real-20261009/commands/registry-copy-python/command.json) / [stdout](../../../runs/interview-real-20261009/commands/registry-copy-python/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/registry-copy-python/stderr.log) |
| registry-copy-retry-python | 1 | 30.981 | [command.json](../../../runs/interview-real-20261009/commands/registry-copy-retry-python/command.json) / [stdout](../../../runs/interview-real-20261009/commands/registry-copy-retry-python/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/registry-copy-retry-python/stderr.log) |
| registry-manifest-python | 0 | 6.889 | [command.json](../../../runs/interview-real-20261009/commands/registry-manifest-python/command.json) / [stdout](../../../runs/interview-real-20261009/commands/registry-manifest-python/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/registry-manifest-python/stderr.log) |
| verified-transfer-python | 1 | 17.977 | [command.json](../../../runs/interview-real-20261009/commands/verified-transfer-python/command.json) / [stdout](../../../runs/interview-real-20261009/commands/verified-transfer-python/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/verified-transfer-python/stderr.log) |
| verified-transfer-v2-django__django-13343 | 0 | 211.752 | [command.json](../../../runs/interview-real-20261009/commands/verified-transfer-v2-django__django-13343/command.json) / [stdout](../../../runs/interview-real-20261009/commands/verified-transfer-v2-django__django-13343/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/verified-transfer-v2-django__django-13343/stderr.log) |
| verified-transfer-v2-django__django-15277 | 0 | 178.470 | [command.json](../../../runs/interview-real-20261009/commands/verified-transfer-v2-django__django-15277/command.json) / [stdout](../../../runs/interview-real-20261009/commands/verified-transfer-v2-django__django-15277/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/verified-transfer-v2-django__django-15277/stderr.log) |
| verified-transfer-v2-django__django-16429 | 0 | 173.779 | [command.json](../../../runs/interview-real-20261009/commands/verified-transfer-v2-django__django-16429/command.json) / [stdout](../../../runs/interview-real-20261009/commands/verified-transfer-v2-django__django-16429/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/verified-transfer-v2-django__django-16429/stderr.log) |
| verified-transfer-v2-python | 0 | 7.139 | [command.json](../../../runs/interview-real-20261009/commands/verified-transfer-v2-python/command.json) / [stdout](../../../runs/interview-real-20261009/commands/verified-transfer-v2-python/stdout.log) / [stderr](../../../runs/interview-real-20261009/commands/verified-transfer-v2-python/stderr.log) |

## 8. 数据审计和未验证范围

`evidence_manifest.json` 记录配置哈希、任务清单、6 次实际 SHA、原始结果／轨迹／patch／checkpoint／Judge／命令日志及镜像 provenance 的 SHA256；`csv_audit.json` 核对 CSV、原始 usage、公式和官方 resolved 字段。审计包括真实 key 精确匹配、常见 credential pattern、HOLDOUT ID 检查及 checkpoint 内 base64 数据。历史受保护文件名仅出现在 plan 的保护清单，不是模型内容；首次宽扫描命中该清单的记录也保留在 audit-history，不删除痕迹。

审计结果 PASS，source main/v3 HEAD、源工作区状态、FINAL_CONFIG.md、tasks.txt、holdout.txt、dev_artifacts 哈希均未变。源仓库原有 runs 删除状态和未跟踪任务书未处理。仅隔离分支新增评测脚本和文档；没有 push 或合并。原始 runs/logs/cache 按原有 ignore 规则仅本地保留，不加入提交。

收尾离线复测仍为 132 passed、2 skipped、5 subtests passed（9.61s；命令 wall=10.201s），不是另一个模型实验。最终 `git diff --check` 通过，没有名称含 interview 的实验容器遗留。Docker system df：Images 20.09GB（16 images）、Containers 42.29MB、Local Volumes 1.262GB、Build Cache 0B；这是整台 daemon 当前占用，不全归因本轮，没有清理用户其他容器／镜像。

HTML 与口述 Markdown 同源，UTF-8，无外部资源，实际用已安装 Edge 在 offline 模式下分别以 1280×900、390×844 打开；没有页面横向溢出、远程请求为 0。内置 Playwright Chromium 缺失，因此使用已安装 Edge，没有下载新浏览器。检查结果见 html_audit.json，已目视核对截图。CSV 检查见 csv_audit.json。

未验证：真实模型完整断点恢复、复杂长任务摘要质量、真实 Feature、v3-multi 效果、未知仓库泛化、多个随机重复。feature_seed 是占位，没有人工审阅的可信 PR 输入，不执行不可信 PR；P1 明确跳过。DEV 是历史用于开发的样本，模型训练污染也无法排除，资源／工具／cache 顺序不同，不能把差异全部归因某个机制。

建议后续先修旧 testbed Python helper 兼容与 shell pipeline 验证退出码边界，再设计独立任务和重复对照。**本轮只提出建议，不修改 Agent、不重跑刷分、不运行 HOLDOUT。**
