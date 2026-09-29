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

唯一一次 CLI run 在 42 steps 后 submit，估算费用 `$0.018110376`。修前
复现失败、修后同一语义判据通过，项目测试为 38 passed / 14 skipped；独立
验证又通过了聚焦判据和全部 52 个 unittest（14 个平台 skip）。最终只修改
`colorama/ansi.py`，full diff 与 production-only diff 相同，原 demo 仓库
HEAD 和 working tree 前后不变。完整 trajectory 与 patch 在
`dev_artifacts/demos/step-3-1-colorama/`。
