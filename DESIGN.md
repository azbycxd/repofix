# RepoFix V3 design

Second-round termination correction: two responses without tool calls produce
`no_tool_call`, never `interrupted`. Completed/terminal runs cannot resume;
only unfinished, genuinely interrupted or provider-error checkpoints can.
Patch collection failures use `runtime_error`. Reports list termination counts.

## Compatibility and loop (M0)

**Problem.** Long-task mechanisms must not change the historical v1 experiment.

**Design.** RepoFixAgent stays the public facade. harness/loop.py routes profiles;
harness/v1.py holds the mechanically extracted loop and registry-based tool
dispatch, with a local legacy state holder. V3 uses a serializable RunState.

**Reference / difference.** Native tool-call loop follows the general Codex
harness pattern. A separate compatibility executor is intentional: frozen error
strings, nudges, timeout and result accounting are part of the v1 contract.

**Validation.** Before refactoring, record a scripted offline session with all
five tools and a truncated observation; compare requests/messages and counters
afterward. Exclude nondeterministic clocks and artifact paths only.

**Limitations.** V1 remains synchronous and is not upgraded by V3 switches.

## 文档参考与适用范围

2026-10-09 阅读官方资料：[Codex rules](https://developers.openai.com/codex/rules)、
[Codex subagents](https://developers.openai.com/codex/subagents)、
[Codex prompting/compaction](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide)、
[Claude Code hooks](https://code.claude.com/docs/en/hooks)、
[Claude Code subagents](https://code.claude.com/docs/en/sub-agents)。
只借鉴公开机制；未复制产品实现。数值参数按任务书或项目自行选择，
不把它们称为两款产品的默认值。下面各节的“验证”对应 tests/test_v3_*.py。

## 工具注册表与调度（M1）

**问题：** 原循环把工具判断、执行和回填混在一起，无法安全并行读取。

**设计：** ToolSpec 保存原生 schema、read_only 和 handler。连续只读调用用
最多四线程执行，写调用构成前后屏障；结果始终按模型调用顺序回填。
grep 使用容器 rg，缺失时回退 grep；FakeEnv 使用内存匹配。

**参考与差异：** 采用任务书建议的原生工具和只读并行。RepoFix 用显式工具
标记决定并行资格，不声称实现了通用命令副作用分析。

**验证：** 并行耗时、顺序、读写混排边界、grep glob 与条数限制。

**局限：** BM25 是运行开始时的代码快照；工具搜索不会自动重建索引。
grep fallback 的 glob 支持受系统 grep 限制。后台进程也可能改动仓库。

## Hooks（M2）

**问题：** 编辑后语法错误及未验证提交需要可组合的反馈。

**设计：** PreToolUse 返回 Allow/Deny/Rewrite；PostToolUse 可改 observation；
PreSubmit 返回 Allow/Block。支持 Python callable 和可信宿主机 argv 命令，
JSON stdin、exit 2/stderr 拦截。syntax_check 比较 porcelain 与脏文件内容哈希，
对任何工具引起的 Python 改动编译。提交需要最后一次改动后的成功验证，
最多阻止三次，再以 submit_forced 结束。重写后的命令重新检查权限。

**参考与差异：** 借鉴 Claude Code 的事件与 exit-2 约定。RepoFix 仅三个事件；
外部命令的其它错误也保守拒绝，而官方产品的部分错误是非阻断的。
PostToolUse 只追加反馈，不能撤销已经运行的 shell。

**验证：** 三事件 callable/外部协议、语法错误、同一文件重复修改、验证顺序、
阻断上限、权限重写和 echo 中的 pytest 不冒充验证。

**局限：** 验证命令识别是辅助启发式，不能证明测试覆盖了需求；外部 hooks
是用户信任的宿主代码。未使用复杂 reproduction 正则推断权威事实。

## 原子补丁与先读后改（M3）

**问题：** 多文件部分写入或基于旧内容编辑会留下不一致仓库。

**设计：** 解析 Add/Update/Move/Delete；所有文件在内存中匹配成功后才写。
匹配依次尝试精确、去行尾空白、去两侧空白、Unicode 标点归一化。
歧义拒绝。写后编译 .py，失败恢复全体原文件及 index。view 记录完整内容哈希；
修改或删除已有文件前必须是最新读取。新建文件不用先读，且显式加入 index。
普通 bash 临时 untracked 文件仍不进入最终 patch。路径限制 /testbed，拒绝
..、外部软链接和 .git。文本匹配由 tools/patch.py 负责，环境 IO 在 workspace.py。

**参考与差异：** 补丁语法参考 Codex apply_patch。RepoFix 的所有文件先准备、
整批语法回滚更严格，目的是让小型任务的失败可理解；并非复制其匹配器。

**验证：** 非法格式、四档匹配、多文件原子性、syntax rollback、move/add/delete、
未读/已读/陈旧哈希、路径逃逸，以及 feature 新文件最终进入 patch。

**局限：** UTF-8 文本补丁；不编辑二进制文件。不把容器中的后台写入当作
跨进程事务锁；read-before-edit 主要防止模型使用已知过期的读取内容。

## Shell 与后台任务（M4）

**问题：** 长测试不应因为前台等待到期而丢失进程或日志。

**设计：** V3 的 bash 默认等待 120s，最大 600s；超时返回 job_id。
独立进程组在容器中写 log/exit 文件；job_output 查询，job_kill 终止整个组。
注入 pager/color/unbuffered 环境；标注退出码、耗时、截断和常见 benign_exit=1。

**参考与差异：** 采用任务书的后台命令/轮询模式。RepoFix 的进程只存活于
当前容器，不提供跨容器后台服务；v1 仍为 60s timeout 终止行为。

**验证：** FakeEnv 超时、轮询、kill、退出码和头格式；Docker 生存性测试标记
docker，默认跳过。

**局限：** 子进程在容器销毁时停止；checkpoint 不会恢复进程。普通 shell
自行启动的守护进程不受 job registry 完整追踪。

## 上下文管理（M5）

**问题：** 大量旧工具输出挤占后续推理所需上下文。

**设计：** 先以上次 usage + 新消息 chars/3 估算。超过阈值时遮蔽旧 tool 内容，
保留调用参数/id，并保存脱敏原文。仍超限才请求固定字段摘要；程序覆盖文件
列表、计划、原任务、diff stat 和最近验证摘要。保留完整最近工具调用配对。
下一请求不重复压缩；三次压缩仍超限以 context_exhausted 停止。

**参考与差异：** Codex 文档说明 compaction 支持长轨迹。这里使用本地可审查
的遮蔽和 JSON 交接，未调用 OpenAI /compact，也没有其隐藏模型状态。

**验证：** 两阶段、usage anchor、artifact、程序事实、工具配对、冷却与耗尽。

**局限：** chars/3 是估算；摘要会损失信息。最近验证输出最多保留末尾 2000
字符作为摘要，完整 observation 仍在轨迹/输出 artifact。没有实测质量结论。

## 计划（M6）

**问题：** 多步骤任务需要能更新、压缩后仍保留的轻量计划。

**设计：** update_plan 只存 step/status，最多一个 in_progress。校验失败不改状态。

**参考与差异：** 参考可更新计划工具的思路；不是 Planner，也不是提交阶段合同。

**验证：** 状态规则、原子更新、压缩后保留。

**局限：** 不强迫调用，不自动验证计划中的完成声明。

## Checkpoint 与恢复（M7）

**问题：** 中断后需要恢复文件和预算，而不是重复执行有副作用的命令。

**设计：** 工具派发前与回填后保存 state；manifest 用临时文件 + rename，
引用带 SHA-256 的 workspace blob。Docker snapshot 是 binary diff 和未跟踪
普通文件 tar。恢复先验证，再在新容器重建；pending call 回填 interrupted。
同名 step 原子更新，损坏最新记录回退。run manifest 只含公开任务和重建信息。

**参考与差异：** 借鉴持久化会话恢复的用户体验；RepoFix 保存本地明文可审查
状态，不复制产品的会话数据库。预算延续，副作用工具不重放。

**验证：** 中断/恢复消息比较、预算/read hash、工作区恢复、损坏回退。

**局限：** 本地 CLI 恢复要求原仓库仍在保存的 HEAD；custom task 重新构建
其固定版本。后台进程不恢复。拒绝不安全 tar 和 untracked .env；快照可能很大，
没有增量压缩或自动保留清理。真实 Docker 恢复还需用户显式集成验证。

## 权限与容器（M8）

**问题：** 明显不必要的发布、下载、安装命令需要可解释的拒绝。

**设计：** JSON/TOML 前缀规则，按保留引号语义的 shell 段落拆分，剥离
env/nohup/timeout。任意 deny 优先，ask 在评测中拒绝、交互 CLI 中询问。
V3 容器丢弃 capabilities，限制权限提升/PID/内存/CPU，始终 network=none。

**参考与差异：** Codex rules 的命令前缀 allow/prompt/forbidden 思路映射成
RepoFix 的 allow/ask/deny。这里只做有限词法解析，不是 Starlark 规则引擎。

**验证：** 包装/引号/复合命令、优先级、ask 模式，以及 Docker create mock。

**局限：** 规则不是安全边界，sh -c/python -c 可绕过。安全边界是容器。
非 root 选项默认关闭，可能需要预先拥有 /testbed 的镜像；cap_drop=ALL 下
chown 的兼容性尚未做 Docker 验证。Build 和可信 hooks 会执行目标代码。

## 子 Agent（M9）

**问题：** 独立探索/验证需要自己的上下文，不能把所有中间日志灌回主循环。

**设计：** 复用 loop，独立 RunState/messages，共享锁保护预算。探索仅
view/grep/search_code/report，8/15/25 steps；验证加 bash，15 steps，结束
恢复仓库改动，发生恢复则不认可该次 PASS。最大深度一，只回报告。可选
PreSubmit verify 最多两轮。去掉角色/系统指令形态的行，并添加来源前缀。

**参考与差异：** 两款产品公开子 Agent 文档描述独立上下文与限定工具。
RepoFix 只支持两个固定角色、固定深度和共享小额预算，没有团队编排。

**验证：** 独立输入、工具限制、报告清洗、预算、深度、工作区恢复和两轮上限。

**局限：** 清洗不是 prompt-injection 防护证明；验证范围仍由模型选择。
受预算锁保护，模型请求串行，文件只读工具仍可并行。默认不启用子 Agent。

## Feature 任务与 Judge（M10）

**问题：** 需要验证新增功能，而不仅是 SWE-bench 修 bug。

**设计：** TaskSpec 将公开描述与隐藏测试隔离；Agent 只收 prompt。feature
提示要求理解约定、计划、验收测试、实现与验证。Builder 在临时 clone 中对
真实 base/merged commit 的测试差异做前后执行；Judge 新建干净容器，应用
production patch 和 hidden test patch，F2P/P2P 全通过才 resolved。

**参考与差异：** 借鉴工具型 Agent 的任务入口；判分约定来自项目/SWE-bench，
不声称使用两款商业产品的评测体系。种子只有占位格式示例，不编造 PR。

**验证：** TaskSpec、公开/隐藏隔离、FakeEnv judge、临时 Git 两提交生成用例。

**局限：** 目前使用 pytest node IDs；跳过不计通过。真实任务需用户提供有效
PR/commit 和人工检查描述泄漏。依赖环境错误与功能缺失都可能使 base 失败，
生成结果仍需人工审阅。YAML 依赖已有 SWE-bench 的 PyYAML；也接受 JSON。

## 实验管线（M11）

**问题：** 多机制必须能开关对照，不能靠主观观察声称效果。

**设计：** 四 variant、固定任务列表/repeats、按顺序运行、独立 artifacts。
输出每 run 一行指标，报告包含 judged denominator、均值、最近秩 P50/P95、
费用和 task-repeat 矩阵。HOLDOUT 先拒绝；输出目录非空拒绝覆盖。

**参考与差异：** 独立 ablation 是本项目实验设计，不是 Codex/Claude 默认机制。

**验证：** 四 variant --fake 完整跑通，fresh FakeEnv 应用 patch 后判断测试；
检查 HOLDOUT 拒绝在创建输出或模型前发生。

**局限：** Fake 指标只证明程序通路，不能推断 coding 能力；本轮没有真实模型
实验或新真实 feature benchmark。比较 feature 时 v1 仍使用冻结提示。

## 遥测与脱敏（M12）

**问题：** 需要区分任务能力、系统中断、上下文和预算耗尽。

**设计：** 枚举终止原因，记录工具/模型耗时、批次、token/cache、压缩、hook、
permission、子 Agent 开销；根 budget 含摘要与子 Agent 请求。summary.json
使用同一 TrajectoryWriter 脱敏。复现遥测仅辅助，不冒充人工确认。

**参考与差异：** 采用事件化观测的一般模式，只使用 JSONL/JSON，不引入
商业 trace 服务、数据库或新的 durable runtime 框架。

**验证：** 结构/缺失 cache 字段、预算停止、secret fixture 和每阶段离线测试；
非 Docker 测试阻断 socket 网络访问。

**局限：** 费用沿用冻结估价常量，非当前账单保证；一次响应可使估值跨过
预算，收到后停止后续工具/请求。不会在未收到 usage 时编造已消耗 token。
