## V1 经验与 V2 设计约束

1. 多角色和固定阶段常使真实任务在修改代码前终止；V2 改为单 Agent loop。
2. 自定义 JSON / Schema 链多次导致结构化输出失败；V2 优先使用原生 Tool Calling。
3. V1 的 SourceEvidence → PatchReadyEvidence 严格交接在两个 unseen task 上重复阻塞；V2 不设额外 Evidence Gate。
4. V1 编辑接口经历过 full-file / line-edit / no-op 问题；V2 后续采用更小、更明确、可验证的编辑接口。
5. V1 在 durable runtime / checkpoint / ledger 上投入过多；V2 先证明修 bug 能力，只保留必要轨迹和沙箱。

## 检索依赖

默认检索为 BM25，只需安装 `requirements.txt`。已停止的 Dense/RRF 实验需
额外安装 `requirements-dense.txt`，并显式传入 `--retrieval-mode dense_rrf`。

## 判分 patch

Agent 完整 diff 保存在 trajectory 和 run artifact 中。提交给 SWE-bench
Harness 的 evaluation patch 会排除 `is_test_path()` 识别出的测试文件修改；
官方判分只评估生产修改，Agent 自己编写的测试单独保存在轨迹中。

## 独立 Reviewer 实验

Reviewer 默认关闭；实验时使用 `--reviewer` 显式开启。它只在首次
`submit` 时独立调用一次，只接收 issue、完整 diff、最后一次有效复现/测试
命令及其输出，并且只返回 `APPROVE` 或 `REJECT: <一句原因>`。

## CLI Usage

安装固定依赖和本地命令后，将 DeepSeek key 放在环境变量或 git ignored
的 `.env` 中：

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
repofix --repo /path/to/local-git-repository --issue "issue description"
```

输入必须是干净的本地 Git working tree。CLI 输出 full diff、过滤测试路径后
的 production-only diff 和简短 summary；默认 artifact 位于
`~/.repofix/runs/<run-id>/`。

## Execution Model

```text
local repo
→ temporary git worktree
→ Python 3.12 Docker build (network allowed)
→ offline Agent runtime in /testbed
→ reproduction / edit / validation
→ full patch + production-only patch + trajectory + report
→ worktree cleanup
```

构建策略只安装固定 pytest、存在时的 `requirements.txt`，以及存在
`pyproject.toml` / `setup.py` / `setup.cfg` 时的项目本身。Agent 运行容器
固定 `network_mode=none`，不会修改原仓库。

安全边界：Docker build 阶段允许联网，并会执行目标仓库的依赖安装、
`setup.py` 或 build backend 代码；因此 local CLI 只应用于用户信任的
repository。项目使用 editable install，运行期 import 始终指向
`/testbed` 当前源码；Agent runtime 本身仍固定为 `network=none`。

## Demo

Step 3.1 使用真实公共项目 Colorama `0.4.6`（commit
`3de9f013df4b470069d03d250224062e8cf15c49`）做了一次
**planted-bug demonstration**，不是 upstream issue。人为 bug 交换了
`Cursor.POS(x, y)` 生成序列中的行列顺序；项目原有测试仍通过，但聚焦行为
判据失败。

Issue 原文：

> Cursor positioning is incorrect when horizontal and vertical coordinates
> differ. Requesting column 4, row 7 places the cursor at row 4, column 7
> instead. Restore the documented coordinate semantics without changing the
> other cursor movement helpers.

第一次 CLI run 在 42 steps 后 submit，估算费用 `$0.018110376`。它在第
24 步已经修正 `/testbed`，但 non-editable install 仍从 site-packages 导入
旧副本，导致第 25/27/31/33 步继续观察到假失败；Agent 最后通过手动插入
`/testbed` 绕过。trajectory review 据此发现 sandbox packaging bug。

将项目安装修成 `pip install -e .` 后，用完全相同的 commit、issue 和默认
配置做了一次固定重验：第 21 步真实复现，第 23 步修改生产代码，第 24 步
相同判据立即通过，不再发生旧副本干扰；第 25 步完整项目测试为 38 passed /
14 skipped，第 28 步 submit。两次都只修改 `colorama/ansi.py`，full diff
与 production-only diff 相同，原 demo 仓库 HEAD 和 working tree 前后不变。

| Demo | Steps | Estimated cost | CLI wall time | Edit → first valid repro |
| --- | ---: | ---: | ---: | --- |
| First, non-editable | 42 | `$0.018110376` | 117.587s | 24 → 34（需手动修正 import path） |
| Fixed, editable | 28 | `$0.014580888` | 99.190s | 23 → 24（立即通过） |

第一次 artifact 保存在 `dev_artifacts/demos/step-3-1-colorama/`，修正版保存在
`dev_artifacts/demos/step-3-1-colorama-editable/`。

## V3：可配置的仓库任务 Harness

V3 在独立 `v3` 分支开发。上述 V1/V2/最终评测记录保留原样；本轮仅完成离线
实现和 FakeModel/FakeEnv 验证，没有产生新的真实模型结果或运行 HOLDOUT。
默认 profile 仍为 `v1`，其金标准覆盖冻结 prompt、五个工具、回填和统计。

```bash
source /home/jiusi/venvs/repofix/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e .
pytest -q

# 以下真实 CLI 命令需自行提供有效 key、Docker 和可信本地仓库。
repofix --repo /path/to/repo --issue "bug description" --profile v1
repofix --repo /path/to/repo --task "feature description" --kind feature --profile v3
repofix --repo /path/to/repo --task "feature description" --kind feature \
  --profile v3 --config /path/to/v3-overrides.json
```

`--issue` 与 `--task` 是同一参数的别名。V3 在原五工具上增加 grep、可选
apply_patch、update_plan、后台 bash/job_output/job_kill，以及显式启用的
explore/verify 子 Agent。已有文件编辑需要先 view；所有补丁文件先匹配，
Python 语法检查失败时整批回滚。新增文件可以进入 production patch。

上下文先遮蔽旧工具输出，必要时才做摘要；计划、真实改动和验证结果重新注入。
每步 checkpoint 保存预算、消息和工作区。每个机制可以在 JSON config 单独
关闭，完整默认值与限制见 [V3_CONFIG.md](V3_CONFIG.md)，设计见
[DESIGN.md](DESIGN.md)，实施日志见 [PROGRESS.md](PROGRESS.md)。

### 离线实验与报告

```bash
python scripts/run_experiment.py --tasks tasks/fake_tasks.json \
  --variants v1,v3-single,v3-multi,v3-nocompact --repeats 1 \
  --out .cache/v3-example --fake
python scripts/report.py --input .cache/v3-example/results.jsonl \
  --out .cache/v3-example/report.md
```

Fake 报告显式标记为程序通路验证，不能解释为真实任务表现。输出目录必须为空，
不会覆盖已有 run。真实 custom-task 实验使用审阅过的 TaskSpec 文件并去掉
`--fake`；脚本在任何运行之前拒绝 `holdout.txt` 中的 ID。v1 对 feature 的
对照仍使用冻结 bugfix 提示；V3 才使用 feature 工作流。

```bash
# 填写真实 PR/commit 后由用户执行；会联网 clone 和 build，不调用模型。
python scripts/build_feature_tasks.py --input tasks/feature_seed.yaml \
  --out /path/to/reviewed-tasks.json --execute
```

seed 中两项都是明确占位示例。Builder 从真实 commit 差异提取隐藏测试并
验证前后结果；生成后必须人工检查公开描述不泄漏实现。Judge 使用全新容器，
只应用 production patch 和隐藏测试，要求 F2P/P2P 全通过；隐藏 patch 不进入
Agent 消息。当前 custom-task 判分适配 pytest node IDs。

### 续跑

```bash
repofix resume /path/to/run-directory
python scripts/run_agent.py --resume /path/to/run-directory
```

从最近完整 checkpoint 重新创建隔离容器并恢复文件/预算；未完成工具回填
interrupted，不重放副作用。损坏的最新记录回退到上一步。CLI 本地仓库必须
仍在原 HEAD；V3 SWE runner 每题的 run directory 是独立子目录。后台进程
不会跨容器续跑。尚未执行真实 Docker 续跑验证。

### 权限规则与 Hooks

`--permissions-file /path/to/rules.toml` 示例：

```toml
default = "allow"
[[rules]]
decision = "deny"
prefix = "git push"
[[rules]]
decision = "ask"
prefix = "pip install"
[[rules]]
decision = "allow"
prefix = "pytest"
```

评测时 ask 视为 deny；交互 CLI 才询问。规则按 shell 段落处理，但不是完整
shell 安全分析；安全边界仍是断网 Docker。V3 默认限制 capabilities、PID、
内存和 CPU，非 root 模式默认关闭且仍需集成验证。

`--hooks-file /path/to/hooks.json` 配置可信宿主 argv 命令：

```json
{
  "PreToolUse": [{"command": ["python", "/path/to/policy_hook.py"], "timeout": 30}],
  "PostToolUse": [],
  "PreSubmit": []
}
```

stdin 是 JSON（event/call/state/result）。exit 0 放行；exit 2 将 stderr
作为理由回给模型。PreToolUse stdout 可为 `{"rewrite": {"command": "..."}}`，
PostToolUse stdout 可为 `{"content": "..."}`。其它错误保守拒绝并记录。
程序内也可给 HookEngine 注册 Python callable。外部 hook 是可信宿主代码，
不运行在 Agent 的断网容器中。

必要 Docker 测试需显式执行，默认测试不联网：

```bash
pytest -q --docker tests/test_v3_shell.py
REPOFIX_DOCKER_INTEGRATION=1 pytest -q --docker tests/test_local_sandbox_integration.py
```

所有新结果写到用户指定的运行目录；不会改写历史 `dev_artifacts/`。

## V4：可靠性合同（隔离实施）

`--profile v4` 显式开启；默认仍为 v1，v1/v3 原有工具 schema 和运行配置不变。
V4 复用现有 Loop/Runtime，逐里程碑实现结构化本地验证、无进展纠偏与恢复边界。
候选补丁（submission）、指定测试通过（local validation）、独立官方判分
（task acceptance）是三个不同结果，不能合并称为 success。
实现与实测进度见 `docs/v4/V4_PROGRESS.md`；未完成机制不得视为已验收。
