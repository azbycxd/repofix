# RepoFix V3 任务书（交给 Codex 一次性执行）

> 使用方式：把本文件放到仓库根目录，然后对 Codex 说：
> **"阅读仓库根目录的 REPOFIX_V3_TASK.md，按其中的里程碑顺序完整执行，不需要中途向我确认。遇到需要人决定的事项，按文中给出的默认值处理并记录到 PROGRESS.md。"**

---

## 0. 背景与目标

RepoFix 当前是一个 SWE-bench bugfix Agent：单个 while 循环（`src/repofix/agent.py` 的 `RepoFixAgent.run()`，约 700 行）、5 个工具（bash / view / str_replace / search_code / submit）、每任务一个断网 Docker 容器（`src/repofix/env.py`），评测用 SWE-bench Verified 的 8 道 Django 题。

面试反馈的两个问题：
1. 它只做 bugfix，不是能"完成任务"的 coding agent。
2. Harness 层太薄：没有上下文压缩、续跑、hooks、权限规则、子 Agent，长任务撑不住。

**V3 目标**：在不推翻现有骨架的前提下，把它升级为"面向真实仓库任务的编码 Agent Harness"：
- 支持两类任务：`bugfix`（现有）和 `feature`（新增功能，有隐藏测试判分）。
- 补齐 Harness 机制：工具注册表与只读并行、hooks、多文件补丁与先读后改、后台命令、上下文压缩、计划、checkpoint/续跑、权限规则与容器加固、子 Agent（探索 / 验证）、结构化观测。
- 每个机制都能通过配置开关单独打开或关闭，并有实验脚本做对照（单 Agent vs 多 Agent、压缩开 vs 关）。

参考设计：Claude Code 与 OpenAI Codex CLI 的公开机制（单循环 + 原生工具；只读工具并行；先清旧工具输出再摘要；hooks 退出即拦截；按命令前缀的 allow/ask/deny；子 Agent 独立上下文只回传摘要）。不要照抄代码，按下面的规格实现。

---

## 1. 硬性规则（必须遵守）

1. **v1 可复现**：新增 `--profile v1|v3`。`v1` 必须与当前冻结配置（`FINAL_CONFIG.md`）行为一致：相同的 system prompt、工具定义、步数和费用上限、截断参数、判分补丁策略。不得修改 `FINAL_CONFIG.md`、`tasks.txt`、`holdout.txt`、`dev_artifacts/` 下的任何文件。默认 profile 仍为 `v1`。
2. **不调用真实模型**：所有新增测试必须离线运行。用 `FakeModelClient`（按脚本返回预设的 assistant 消息 / tool_calls / usage）和 `FakeEnv`（内存文件系统 + 可脚本化的命令输出）。需要 Docker 的测试加 `@pytest.mark.docker`，默认跳过。
3. **密钥**：不得把任何 API key、`.env` 内容写入代码、测试、日志、提交信息或文档。沿用 `TrajectoryWriter` 的脱敏。
4. **不跑封存集**：不得运行 `holdout.txt` 中的任何任务。
5. **小步提交**：每个里程碑结束时 `pytest -q` 全绿后单独提交，提交信息格式 `v3(Mx): <一句话>`。在新分支 `v3` 上工作，不要推送、不要合并到 main。
6. **进度日志**：在仓库根目录维护 `PROGRESS.md`，每个里程碑记录：做了什么、测试结果、偏离规格之处及理由、未完成项。
7. **时间不够时**：宁可少做几个里程碑，也要保证已做的里程碑完整、测试通过、文档更新。优先级见第 4 节。不要留下半成品代码。
8. **不引入重依赖**：除 `pytest`、标准库、现有依赖外，最多新增 `tiktoken`（可选，用于 token 估算；不可用时退回"字符数 / 3"的估算）。

---

## 2. 目标架构

把 `agent.py` 中过长的 `run()` 拆开。建议目录（可按实际调整，但要在 `DESIGN.md` 里说明）：

```
src/repofix/
  agent.py            # 保留 RepoFixAgent 外壳与 v1 兼容入口
  harness/
    loop.py           # 主循环：请求模型 → 解析 tool_calls → 调度执行 → 回填 → 终止判断
    model.py          # 模型客户端协议（真实 OpenAI 兼容客户端 + FakeModelClient）
    state.py          # RunState：messages、plan、budget、step、pending_calls、file_reads、termination
    tools/
      registry.py     # ToolSpec(name, schema, read_only, handler)；按 profile 组装工具集
      shell.py        # bash（含后台任务）、job_output、job_kill
      files.py        # view、str_replace、apply_patch、grep
      search.py       # search_code（包装现有 BM25）
      plan.py         # update_plan
      submit.py
      agents.py       # explore / verify 子 Agent 工具
    hooks.py          # PreToolUse / PostToolUse / PreSubmit 钩子框架 + 内置钩子
    context.py        # ContextManager：token 估算、遮蔽、摘要、压缩后重注入
    permissions.py    # 命令规则解析与判定
    checkpoint.py     # 每步状态落盘、resume
    subagent.py       # 子 Agent 运行器（复用 loop，独立 state，受限工具集）
    telemetry.py      # 事件与指标汇总
  tasks/
    spec.py           # TaskSpec：id、kind(bugfix|feature)、repo、base_commit、prompt、hidden_tests
    prompts.py        # 按 kind 生成系统提示和首条用户消息
    judge.py          # 自定义任务的判分：应用隐藏测试 → 跑指定用例 → resolved
```

配置：新增 `HarnessConfig`（dataclass），字段全部有默认值，`profile="v1"` 时等价于现在的 `AgentConfig`。所有新机制都有独立开关，例如：
`parallel_readonly`, `hooks_enabled`, `apply_patch_enabled`, `read_before_edit`, `background_shell`, `context_management`, `plan_tool`, `checkpointing`, `permissions_file`, `sandbox_hardening`, `subagents`(none|explore|verify|both), `task_kind`。

---

## 3. 里程碑（按顺序执行）

每个里程碑都写明：做什么 → 验收标准（必须有对应测试）。

### M0 重构不改行为（必须）
- 把 `run()` 拆成 `harness/loop.py` + 工具注册表 + `RunState`；保留 `RepoFixAgent` 对外接口和 `scripts/run_agent.py`、`cli.py` 的调用方式。
- 新建 `FakeModelClient`、`FakeEnv`。
- **验收**：新增"金标准"测试：用一段脚本化的 FakeModel 对话（至少包含 view、str_replace、bash、search_code、截断触发、submit），在 `profile=v1` 下跑重构前后的循环，`messages` 序列、终止状态、统计字段（provider_calls、tool_call_count、truncations 等）完全一致。先在重构前的代码上录制期望输出，再重构。

### M1 工具注册表 + 只读工具并行（必须）
- `ToolSpec.read_only` 标记：view、search_code、grep、job_output、explore 为只读；bash、str_replace、apply_patch、submit、update_plan、verify 为非只读。
- 同一响应里连续的只读调用用线程池并发执行，非只读调用串行；**回填顺序必须与模型返回的 tool_call 顺序一致**。
- 新增只读工具 `grep(pattern, path_glob?, max_results)`：在容器内用 `rg`（不存在则 `grep -rn`）实现，结果带文件和行号，限制条数。
- **验收**：测试并发执行（FakeEnv 中给只读工具加 sleep，验证总耗时接近最大值而非求和）、回填顺序、只读与非只读混排时的串行边界。

### M2 Hooks（必须）
- 三个事件：`PreToolUse(call, state) -> Allow | Deny(reason) | Rewrite(args)`、`PostToolUse(call, result, state) -> result'`、`PreSubmit(state) -> Allow | Block(reason)`。
- 钩子可以是 Python 可调用对象，也可以是外部命令（stdin 传 JSON，退出码 2 = 拦截，stderr 为理由），与 Claude Code / Codex 的 hook 约定一致。
- 内置钩子：
  1. `syntax_check`（PostToolUse）：**任何**工具（包括 bash）执行后，用 `git status --porcelain` 对比前后变化，对新改动的 `.py` 文件跑 `py_compile`，失败时把错误附加到工具结果里告诉模型（不自动回滚，回滚仍只在 str_replace / apply_patch 内部做）。
  2. `command_policy`（PreToolUse）：调用 M8 的权限判定。
  3. `verify_before_submit`（PreSubmit）：最后一次文件改动之后，若没有任何"验证命令"（pytest / python -m pytest / 复现脚本 / tox / 用户配置的命令模式）以退出码 0 完成，则拦截提交，把理由作为工具结果回给模型，继续循环。同一任务最多拦截 3 次，之后放行并在遥测中记 `submit_forced`（防死循环，参考 Claude Code 对 Stop hook 的连续拦截上限）。
- **验收**：每个内置钩子的单测；外部命令钩子的退出码协议测试；拦截上限测试。

### M3 编辑：多文件补丁 + 先读后改（必须）
- 新工具 `apply_patch(patch)`，格式与 Codex 一致：
  ```
  *** Begin Patch
  *** Add File: path
  +line
  *** Update File: path
  *** Move to: new_path        (可选)
  @@ optional context
   context line
  -old
  +new
  *** Delete File: path
  *** End Patch
  ```
- 上下文匹配四档逐级放宽：精确 → 去行尾空白 → 去两端空白 → Unicode 标点归一化。
- **原子性**：先解析并在内存中对所有文件完成匹配和生成新内容，全部成功才写入；任一失败则一个文件都不写，返回"哪个文件、哪段上下文没找到"。（这一点比 Codex 更严格，DESIGN.md 里说明理由。）
- 写入后对 `.py` 文件跑 `py_compile`，失败则整体回滚。
- **先读后改**（`read_before_edit`）：记录每个文件被 view 时的内容哈希；str_replace / apply_patch 的 Update 目标若从未被 view 过，或当前哈希与 view 时不同（被 bash 改过），则拒绝并提示先 view。新建文件不需要。
- 所有路径限制在 `/testbed`，拒绝 `..` 和指向外部的软链接。
- **验收**：解析器单测（含非法格式）、四档匹配单测、原子回滚测试、先读后改的三种情况、路径逃逸测试。

### M4 Shell：后台任务与输出格式（必须）
- `bash(command, timeout?, run_in_background?)`：
  - 默认超时 120 秒，上限 600 秒。超时时**不杀进程**，转为后台任务并返回 `job_id`（`background_shell=True` 时；v1 保持 60 秒超时直接杀）。
  - `run_in_background=True` 立即返回 `job_id`，输出写到容器内 `/tmp/repofix_jobs/<id>.log`。
  - 新工具 `job_output(job_id, tail_lines)`（只读）、`job_kill(job_id)`。
- 命令统一注入环境变量：`PAGER=cat GIT_PAGER=cat NO_COLOR=1 TERM=dumb PYTHONUNBUFFERED=1`。
- 输出头部统一为：`exit_code: N | duration: Xs | truncated: yes/no`（或 `still running: job_id=...`）。
- grep / diff / test 等命令退出码 1 不算失败（在头部标注 `benign_exit`）。
- **验收**：FakeEnv 上的超时转后台、轮询、kill、输出头格式测试；Docker 集成测试（标记跳过）。

### M5 上下文管理（必须）
- `ContextManager.maybe_compact(state)` 在每次请求模型前调用。
- token 估算：优先用上一次响应 usage 的 prompt_tokens + 之后新增消息的估算。
- 配置：`context_window`（默认 64000，便于在小任务上也能触发做实验）、`compact_threshold`（默认 0.75）、`keep_recent_tool_results`（默认 4）。
- 两阶段：
  1. **遮蔽**：把较早的 tool 消息内容替换为 `[RepoFix: 已省略 N 字符的旧输出，全文见 <artifact 路径>]`，保留 tool_call_id 与调用参数；全文写到 run 目录。只遮蔽，不改 assistant 消息。
  2. **交接摘要**：遮蔽后仍超过阈值时，调用一次模型生成固定字段的摘要（JSON）：`goal`、`constraints`、`done`、`verified_facts`、`failed_attempts`（含原因）、`files_changed`、`next_steps`。`files_changed` 和"最近一次测试结果"由程序填，不让模型写。新的 messages = 原 system + 原任务消息 + 一条"压缩摘要"user 消息 + 最近 K 条消息。
- 压缩后重新注入：任务原文、当前计划（M6）、`git diff --stat`、最近一次验证命令及结果摘要。
- 防抖：压缩后下一次请求仍超阈值时不重复压缩；连续 3 次压缩后仍超阈值则以 `context_exhausted` 终止。
- 遥测记录每次压缩：前后 token 估算、遮蔽条数、是否调用摘要。
- **验收**：用小窗口 + FakeModel 测试两阶段触发、摘要字段注入、重注入内容、防抖与终止。

### M6 计划工具（应做）
- `update_plan(steps=[{step, status}])`，status ∈ pending / in_progress / completed，同时最多一个 in_progress，违反则返回错误。
- 计划存在 `RunState` 并在压缩后重注入；不是硬门，不调用也能提交（V1 教训：不要做成阶段合同）。
- **验收**：校验规则、压缩后保留。

### M7 Checkpoint 与续跑（必须）
- 每步结束后把 `RunState` 写成 `run_dir/checkpoints/step-XXXX.json`（messages、plan、budget、计数器、file_reads 哈希、未完成的 tool_calls），并保存工作区快照：`git diff --binary HEAD` + 未跟踪文件打包（`git ls-files --others --exclude-standard` 后 tar），存到容器外。写入用"写临时文件 + rename"保证原子。
- `repofix resume <run_dir>`（以及 `scripts/run_agent.py --resume`）：重建容器 → 应用快照 → 加载最后一个完整 checkpoint → 未完成的 tool_call 回填为 `[interrupted: tool call did not complete before the run stopped]` → 继续循环。预算与步数从 checkpoint 继续累计。
- 有副作用的工具不重放；只读工具可以让模型自己重新调用。
- **验收**：FakeEnv 测试"第 k 步后模拟崩溃 → resume → 最终 messages 与不中断运行一致（除中断标记外）"；快照应用测试；损坏的最后一个 checkpoint 时回退到上一个。

### M8 权限规则与容器加固（必须）
- 规则文件（TOML 或 JSON），例如：
  ```toml
  [[rules]]  decision = "deny"  prefix = "git push"
  [[rules]]  decision = "deny"  prefix = "rm -rf /"
  [[rules]]  decision = "deny"  prefix = "curl"
  [[rules]]  decision = "ask"   prefix = "pip install"
  [[rules]]  decision = "allow" prefix = "pytest"
  ```
- 判定：把命令按 `&&`、`||`、`;`、`|`、换行拆分，剥掉 `timeout`、`nohup`、`env X=Y` 等前缀包装；任一段命中 deny → 拒绝；任一段命中 ask → 评测模式下按 deny 处理、交互 CLI 下询问用户；否则允许（默认策略可配置）。拒绝理由作为工具结果回给模型。在 DESIGN.md 里写明：这层规则不是安全边界（`sh -c`、`python -c` 可以绕过），安全边界是容器。
- 容器加固（`sandbox_hardening=True` 时）：`cap_drop=["ALL"]`、`security_opt=["no-new-privileges"]`、`pids_limit=512`、`mem_limit="4g"`、`nano_cpus=2e9`；保持 `network_mode="none"`。非 root 运行作为可选项 `sandbox_user`（需要对 /testbed chown，可能较慢，默认关闭，写进 PROGRESS.md 的待验证项）。
- **验收**：规则解析与复合命令拆分的单测（含包装剥离、引号内的分隔符不拆）；容器参数的单测（mock docker client 检查 create 参数）。

### M9 子 Agent（必须）
- 复用 `harness/loop.py`，子 Agent 有独立 `RunState`、独立 messages、受限工具集、独立步数上限，共享总预算；深度上限 1（子 Agent 不能再起子 Agent）。
- `explore(question, thoroughness=quick|medium|thorough)`（只读）：工具集 = view、grep、search_code；步数上限 8 / 15 / 25；最后必须调用 `report(summary)`，摘要不超过 1500 字符，只返回摘要给主 Agent。
- `verify(focus?)`（非只读工具，但子 Agent 本身被约束为不改代码）：工具集 = view、grep、search_code、bash；任务是"找到与当前 diff 相关的测试并运行，必要时写临时测试到 /tmp，给出 PASS / FAIL 和证据"。运行前保存工作区快照，结束后若 `/testbed` 被改动则恢复并在报告里注明。返回结构化结果 `{verdict, commands_run, evidence}`。
- `subagents=verify|both` 时，`verify_before_submit` 钩子可以配置为调用 verify 子 Agent：FAIL 则拦截提交并把证据回给主 Agent（最多 2 轮）。这直接修复 V2 Reviewer"没有工具、只看最近一条命令"导致误放行的问题。
- 子 Agent 的输出在回填前做清洗：去掉看起来像系统指令或角色标记的行，加前缀 `[subagent report]`。
- **验收**：FakeModel 驱动的 explore 与 verify 流程测试；verify 改动工作区后被恢复；深度限制；预算共享。

### M10 任务类型与功能任务评测（必须）
- `TaskSpec`：`id, kind, repo_url, base_commit, prompt, hidden_test_patch, fail_to_pass, pass_to_pass, setup_commands`。
- 提示词按 kind 区分：
  - `bugfix`：保留现有"先复现再修改"。
  - `feature`：先读代码理解约定 → 用 update_plan 列出步骤 → 先写或找到能体现需求的验收测试 → 实现（可以跨多个文件、新建文件）→ 运行相关测试与已有测试 → 提交。不允许修改已有测试来让实现通过。
- `scripts/build_feature_tasks.py`：输入一个 YAML 列表（每项：仓库 URL、PR 合并前的 base commit、PR 合并后的 commit、PR 描述），自动生成 TaskSpec：`hidden_test_patch` = 两个 commit 之间测试路径（复用 `is_test_path()`）的 diff；`fail_to_pass` = 在 base + 隐藏测试上失败、在合并后 commit 上通过的用例（需要联网和 Docker，由用户运行）。生成后要求人工确认任务描述不泄露实现。
- `tasks/judge.py`：在干净容器中 base_commit + Agent 的生产补丁 + 隐藏测试补丁，运行 fail_to_pass 与 pass_to_pass，全部满足才 resolved（与 SWE-bench 口径一致）。
- 提供 `tasks/feature_seed.yaml` 模板，写好字段说明和 2 个**格式示例**（用明显的占位符，不要编造真实 commit）。
- **验收**：TaskSpec 校验、提示词生成、judge 在 FakeEnv 上的判定逻辑、build 脚本在本地临时 git 仓库（测试里现造两个 commit）上的端到端测试。

### M11 实验脚本（必须）
- `scripts/run_experiment.py --tasks <file> --variants v1,v3-single,v3-multi,v3-nocompact --repeats N --out <dir>`：
  - `v3-single`：v3 全部机制，subagents=none。
  - `v3-multi`：v3 + explore + verify。
  - `v3-nocompact`：v3-single 关闭 context_management（用于压缩消融）。
- 每次运行输出一行 JSON：task、variant、repeat、resolved（若有判分）、submitted、termination_reason、steps、tool_calls、subagent_calls、compactions、hook_blocks、prompt_tokens、completion_tokens、cache_hit_tokens、estimated_cost、wall_seconds。
- `scripts/report.py`：汇总成 Markdown 表（按 variant 的通过率、均值、P50/P95 步数与耗时、成本），并输出"各 variant 在每个任务上的结果矩阵"。
- 封存规则沿用：实验脚本拒绝运行 holdout 列表中的任务。
- **验收**：用 FakeModel + FakeEnv 跑通整个实验管线并生成报告（证明管线可用；真实数字由用户用真实 key 运行）。

### M12 观测（应做）
- 终止原因枚举：`submitted`、`submit_forced`、`max_steps`、`max_cost`、`context_exhausted`、`provider_error`、`interrupted`。
- 每步记录：模型延迟、各工具耗时、并行批次大小、token、缓存命中、压缩事件、钩子决策、权限决策、子 Agent 起止与花费。
- run 结束写 `summary.json`。

### M13 文档（必须）
- `DESIGN.md`：每个机制一节，固定结构：**问题 → 设计 → 参考了 Claude Code / Codex 的哪一点、哪里不同及理由 → 如何验证 → 已知局限**。
- `README.md`：新增 V3 章节（如何用 profile、如何跑实验、如何 resume、如何写权限规则和钩子）；保留 V1/V2 原有内容和数字，不改写历史结果。
- `V3_CONFIG.md`：列出 v3 默认参数及来源（哪些是参考两款产品的公开默认值，哪些是自定）。
- `PROGRESS.md`：最终状态、未完成项、需要用户运行的命令清单。

---

## 4. 优先级（时间不够时按此取舍）

1. M0、M1、M2、M3、M5、M9、M10、M11、M13 —— 核心，必须完成。
2. M4、M7、M8 —— 应完成。
3. M6、M12 —— 可简化（M12 至少做终止原因和 summary.json）。

如果某个里程碑无法在时间内完成，回退该里程碑的未完成改动，保证主干可用，并在 PROGRESS.md 写明。

---

## 5. 完成标准（Definition of Done）

- `pytest -q` 全部通过（docker 标记的测试允许跳过）；新增测试覆盖每个里程碑的验收点。
- `--profile v1` 金标准测试通过。
- `python scripts/run_experiment.py ... --fake` 能跑完并产出报告。
- `repofix --repo <path> --task "<描述>" --kind feature --profile v3` 的 CLI 参数可用（真实运行需要用户的 key 和 Docker）；原有 `--issue` 参数保留为 `--task` 的别名，旧用法不变。
- DESIGN.md、README.md、V3_CONFIG.md、PROGRESS.md 已更新。
- 所有工作在 `v3` 分支，未推送。
