# RepoFix V3 修复任务书（第二轮，交给 Codex 一次性执行）

> 使用方式：放到仓库根目录，在 `v3` 分支上对 Codex 说：
> **"阅读 REPOFIX_V3_FIX.md，按顺序完成全部修复，不需要中途确认；每一项修完跑 pytest 并单独提交。"**

第一轮（commit dd15192）经过独立审查：v1 金标准已用重构前的 main 代码复核一致，各里程碑都不是空壳。但有一个只在 Docker 下出现的真实 bug、若干与规格不符的行为、测试只覆盖假环境分支的问题，以及代码可读性问题。本轮只修这些，不加新功能。

硬性规则与第一轮相同：不改 v1 行为（金标准测试必须一直绿）、不调用真实模型、不跑封存集、不写密钥、在 `v3` 分支小步提交、提交信息格式 `v3-fix(Fn): <一句话>`、更新 PROGRESS.md。

---

## F1 大文件编辑在 Docker 下失败（最高优先级）
- 问题：`harness/workspace.py` 的 `RepoFiles.apply` 把整个新文件内容拼进 `python -c '...'` 的一个参数，Linux 单个参数上限约 128 KiB，超过约 100 KB 的文件在 Docker 下 str_replace / apply_patch 都会报 "Argument list too long"。离线测试走 FakeEnv 分支所以没发现。
- 修法：先用 `env.write_text_file`（put_archive）把 updates 写成容器内 `/tmp/repofix_apply_<uuid>.json`，helper 脚本从文件读取，结束后删除；与 `restore_workspace` 的做法保持一致。
- 验收：新增测试，用 `test_v3_workspace_backend.py` 的方式（在本地真实执行 helper 源码）对一个 300 KB 的文件做替换和多文件补丁，成功且原子回滚仍有效。

## F2 编辑后没刷新"已读哈希"
- 问题：`runtime.py` 的 str_replace / apply_patch 成功后没有更新 `file_reads`，导致 Agent 改完一个文件后再改它会被拒绝，必须重新 view；`test_v3_patch.py` 还断言了这个错误行为。规格只要求拦截"读后被外部（如 bash）改过"。
- 修法：编辑成功后把新内容的哈希写回 `file_reads`；Add/Move 的新路径也登记。
- 验收：修改测试：连续两次编辑同一文件无需重新 view；bash 改动后再编辑仍被拒绝。

## F3 `benign_exit` 把测试失败当成正常
- 问题：`tools/shell.py` 用正则匹配 `pytest|tox|unittest|test`，导致 pytest 失败（退出码 1）被标成 `benign_exit`。
- 修法：只看第一个命令词（剥掉 `timeout`、`env X=Y` 等包装后），且只对 `grep`、`rg`、`egrep`、`fgrep`、`diff`、`test`、`[`、`git diff`、`git grep`、`find` 生效。管道命令以最后一段为准。
- 验收：`pytest -q` 退出 1 → 不是 benign；`grep foo x` 退出 1 → benign；`python test.py` 退出 1 → 不是 benign。

## F4 终止原因错误
- 问题：`loop.py` 中模型连续两次不调用工具时记为 `interrupted`，resume 会把 `interrupted` 当成可续跑，实验统计也被污染。
- 修法：新增终止原因 `no_tool_call`；只有真正的 KeyboardInterrupt / 进程中断才是 `interrupted`。更新 telemetry、report、DESIGN.md。
- 验收：对应测试；resume 对 `no_tool_call` 的 run 拒绝续跑并给出提示。

## F5 verify 子 Agent 输出未清洗、未限长
- 问题：`subagent.py` 把 `commands_run[*].output` 原样回给主 Agent。
- 修法：每条输出过 `clean_report` 并截断到 1,000 字符，`evidence` 总长不超过 1,500 字符；主 Agent 拿到的只有 verdict、命令列表、截断后的证据。
- 验收：构造含伪系统指令和超长输出的子 Agent 结果，确认被清洗和截断。

## F6 verify 改动工作区时的行为与文档不一致
- 问题：当前只要 `/testbed` 被子 Agent 改过就强制 FAIL，比规格更严格，且 PROGRESS.md 写的是"无偏离"。
- 修法：改为"恢复工作区 + 在报告中注明 `workspace_restored: true`，verdict 仍按测试结果给出"，与规格一致。
- 验收：测试覆盖"子 Agent 写了临时文件到 /testbed 但测试通过"的情况。

## F7 Python 钩子在真实运行中无法注册
- 问题：`Runtime` 构造 `HookEngine` 时没传入 pre/post/submit 列表，只有 `hooks_file` 外部命令钩子可用；`syntax_check`、`verify_before_submit` 是硬编码而不是注册的钩子。
- 修法：内置钩子改为在 `HookEngine` 里按配置注册；`RepoFixAgent` / `HarnessConfig` 提供注册 Python 钩子的入口。
- 验收：通过 `run_v3` 整个循环跑一次：自定义 PreToolUse 钩子拒绝某命令 → 模型收到拒绝理由；verify_before_submit 连续拦截 3 次后 `submit_forced`。

## F8 权限拆分把重定向当成分隔符
- 问题：`permissions.py` 把 `2>&1` 里的 `&` 当成命令分隔符，`default="deny"` 时 `pytest 2>&1 | tail` 会被误拒。
- 修法：拆分前先识别 `2>&1`、`>&`、`&>`、`>`、`<` 等重定向记号，不参与分隔；`$(...)` 和反引号内的命令也要提取出来单独判定（deny 规则对它们生效）。
- 验收：`pytest 2>&1 | tail -5` 被正确拆成 `pytest`、`tail -5`；`echo $(git push)` 命中 deny。

## F9 测试只覆盖假环境分支
- 问题：生产代码里有 12 处 `hasattr(env, "files")` 分支，v3 的大部分测试走的是假分支，不是 Docker 下真正执行的代码（grep、后台任务管理器、补丁写入）。
- 修法：把 FakeEnv 改成实现与 DockerEnv 相同的 `execute` 契约（在本地临时目录里真实执行命令，`/testbed` 映射到临时目录），删掉生产代码里所有 `hasattr(env, "files")` 分支。做不到全部删除时，至少对 grep、后台任务、补丁写入三条路径用"本地真实执行 helper 源码"的方式测试。
- 验收：`grep -rn "hasattr(.*env.*files" src/` 为空（或在 PROGRESS.md 说明剩余项及理由）；新增的本地执行测试覆盖 rg 不存在时退回 `grep -rn`、后台任务超时转后台再轮询再 kill。

## F10 运行开销
- 每步 trace 只写本步新增的 events，不写累计列表。
- checkpoint 每步保存一次，另外只在会改文件的工具（bash、str_replace、apply_patch、verify）执行后保存；不在只读工具后保存。
- 后台任务轮询用退避（0.1 → 0.2 → 0.5 → 1 秒封顶），只读日志尾部。
- 子 Agent 复用主 Agent 的 BM25 索引，不重复构建。
- 验收：对应单测（例如统计 FakeEnv 上 checkpoint 写入次数）。

## F11 可读性重构（必须做，用户要在面试中讲这份代码）
- `harness/v1.py`：把机械改写成 `s.xxx` 属性的写法恢复成普通局部变量；不允许出现超过 120 字符的行；`for s.tool_call in ...` 这种写法改掉；补上缺失的 `from typing import Any`。金标准测试必须保持绿色。
- 所有函数内 import 移到文件顶部；如果有 `agent` ↔ `harness` 循环引用，把 `SYSTEM_PROMPT`、`AgentConfig` 等共享定义移到独立模块（例如 `repofix/core.py`）来解开。
- `loop.py` 不再给 `agent._execute_tool` 打猴子补丁、不再用别的对象当 `self` 调 `V1Loop.run`；改成显式传参。
- 把 `runtime.py` 里的工具处理函数拆到规格里的 `tools/` 模块：`shell.py`、`files.py`、`search.py`、`plan.py`、`submit.py`、`agents.py`；`runtime.py` 只负责组装和分发。
- 去掉 `__import__('shlex')` 之类写法。
- 全项目跑一次格式化（`ruff format` 或 `black`，行宽 100；如果不能安装就手工保证），不改变行为。
- 验收：`pytest -q` 全绿；金标准测试绿；`awk 'length > 120' src/repofix/**/*.py` 无输出。

## F12 文档更正
- PROGRESS.md：如实列出与规格的偏离（包括本轮修复前的行为和修复后的结果），删除"No deviations"。
- DESIGN.md：更新受影响的章节（编辑哈希刷新、benign_exit、终止原因、权限拆分、verify 恢复语义、钩子注册方式）；补一节"已知局限"：上下文遮蔽后的占位路径在宿主机上，模型无法直接读取（给出改进方案：同时把全文写到容器内 `/tmp/repofix_artifacts/`）。
- 如果时间允许，顺手实现上面这条改进，并加测试。

---

## 完成标准
- `pytest -q` 全绿，金标准测试绿。
- F1–F12 每项一个提交。
- PROGRESS.md 末尾给出：本轮修复清单、仍未覆盖的路径（例如 Docker 下的完整 resume）、用户需要在本机运行的验证命令（含 `pytest -m docker`）。
