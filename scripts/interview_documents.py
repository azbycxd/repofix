"""Render factual reports and a standalone speaking-notes HTML from saved evidence.

No model, Docker, or Judge invocation occurs here. Numerical tables are derived;
case interpretations below cite the immutable original trajectory step numbers.
"""

from __future__ import annotations

import html
import json
import re
import shlex
import sys
from datetime import datetime
from html.parser import HTMLParser

from interview_eval import OUT, ROOT, load_key, sha as file_sha
from interview_export import DOCS, read_json


def table(headers, rows):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join([
        '| ' + ' | '.join(headers) + ' |',
        '| ' + ' | '.join('---' for _ in headers) + ' |',
        *['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows],
    ])


def fixed(value, digits=3):
    return f'{value:.{digits}f}'


def verdict(row):
    if row['resolved'] is None:
        return row['judge_status']
    return 'RESOLVED' if row['resolved'] else 'UNRESOLVED'


def relative_link(path, text=None):
    return f'[{text or path}](../../../{path})'


def inline(text):
    # The speaking source uses only plain text, inline code and bold emphasis.
    text = html.escape(text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)


def render_html(markdown):
    """Small, deterministic renderer for the speaking document's Markdown subset."""
    blocks, paragraph = [], []

    def flush():
        if paragraph:
            blocks.append('<p>' + inline(' '.join(paragraph)) + '</p>')
            paragraph.clear()

    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush()
        elif line.startswith('#'):
            flush()
            level = len(line) - len(line.lstrip('#'))
            blocks.append(f'<h{level}>' + inline(line[level:].strip()) + f'</h{level}>')
        elif line.startswith('|'):
            flush()
            grid = []
            while i < len(lines) and lines[i].startswith('|'):
                cells = [part.strip() for part in lines[i].strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', c) for c in cells):
                    grid.append(cells)
                i += 1
            head = '<tr>' + ''.join('<th>' + inline(c) + '</th>' for c in grid[0]) + '</tr>'
            body = ''.join('<tr>' + ''.join('<td>' + inline(c) + '</td>' for c in row) + '</tr>' for row in grid[1:])
            blocks.append('<div class="table-wrap"><table><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>')
            continue
        else:
            paragraph.append(line)
        i += 1
    flush()
    css = '''
:root{color-scheme:light;font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f3f5f8;color:#202a38}
*{box-sizing:border-box}body{margin:0}main{max-width:900px;margin:32px auto;padding:28px 42px 50px;background:white;border-radius:12px;border-top:5px solid #236a82;box-shadow:0 4px 24px #18304912}
h1{font-size:1.9rem;line-height:1.4;margin:0 0 24px}h2{font-size:1.35rem;margin:36px 0 12px;color:#125771;border-bottom:1px solid #dce4ec;padding-bottom:9px}h3{font-size:1.08rem;margin-top:25px}p{line-height:1.85;margin:12px 0;overflow-wrap:anywhere}code{background:#eff3f6;border-radius:4px;padding:2px 5px;font-size:.9em;overflow-wrap:anywhere}strong{color:#8c3a12}.table-wrap{overflow-x:auto}table{border-collapse:collapse;min-width:540px;width:100%;font-size:.93rem}td,th{border:1px solid #dce4ec;padding:10px;text-align:left;white-space:nowrap}th{background:#eaf2f6}footer{margin-top:36px;border-top:1px solid #dce4ec;padding-top:15px;font-size:.85rem;color:#536577}
@media(max-width:600px){main{margin:0;border-radius:0;padding:22px 18px 36px}h1{font-size:1.55rem}p{font-size:1rem}}
@media print{body{background:white}main{margin:0;box-shadow:none;border:0;padding:0}h2,h3{break-after:avoid}table{font-size:10pt}}
'''
    return '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>RepoFix V3｜百度一面口述补充·真实评测</title><style>' + css + '</style></head><body><main>' + '\n'.join(blocks) + '<footer>单文件离线文档：无外部字体、图片、脚本或 CDN。可直接复制到手机、平板或电脑打开；无需伴随 CSV 或服务器。</footer></main></body></html>\n'


class HTMLAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external = []
        self.h1 = 0
        self.tables = 0
        self.viewport = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ('script', 'iframe', 'img', 'link') or 'src' in attributes:
            self.external.append((tag, attributes))
        self.h1 += tag == 'h1'
        self.tables += tag == 'table'
        self.viewport |= tag == 'meta' and attributes.get('name') == 'viewport'

    def handle_data(self, data):
        self.text.append(data)


def main():
    data = read_json(DOCS / 'derived_data.json')
    plan = read_json(OUT / 'plan.json')
    manifest = read_json(DOCS / 'evidence_manifest.json')
    rows = data['rows']
    assert len(rows) == 6 and all(r['judge_status'] == 'JUDGED' for r in rows)
    assert all(r['resolved'] is True for r in rows), 'Revisit narrative if underlying evidence changes'
    assert len({r['git_sha'] for r in rows}) == 1
    commands = {c['label']: c for c in data['commands']}
    totals = {}
    sums = ('steps', 'provider_calls', 'tool_calls', 'prompt_tokens', 'completion_tokens',
            'cache_hit_tokens', 'estimated_usd', 'agent_wall_seconds', 'agent_process_wall_seconds')
    for profile in ('v1', 'v3'):
        group = [r for r in rows if r['profile'] == profile]
        totals[profile] = {key: sum(r[key] for r in group) for key in sums}
        totals[profile]['judge_wall_seconds'] = commands[f'judge-{profile}']['wall_seconds']
    combined = {key: sum(g[key] for g in totals.values()) for key in totals['v1']}
    elapsed = (datetime.fromisoformat(commands['judge-v3']['ended']) - datetime.fromisoformat(plan['created'])).total_seconds()
    sha = rows[0]['git_sha']
    step_delta = totals['v3']['steps'] - totals['v1']['steps']
    prompt_delta = totals['v3']['prompt_tokens'] - totals['v1']['prompt_tokens']
    cost_delta = totals['v3']['estimated_usd'] - totals['v1']['estimated_usd']
    full_table = table(
        ['Task', 'Profile', 'Official Judge', 'Terminal / submitted', 'Steps / Provider / Tools', 'Prompt', 'Cache hit', 'Completion', 'Est. USD', 'Loop / process s'],
        [[r['task_id'], r['profile'], verdict(r), f"{r['termination_reason']} / {r['submitted']}",
          f"{r['steps']} / {r['provider_calls']} / {r['tool_calls']}", r['prompt_tokens'], r['cache_hit_tokens'],
          r['completion_tokens'], fixed(r['estimated_usd'], 9),
          f"{fixed(r['agent_wall_seconds'])} / {fixed(r['agent_process_wall_seconds'])}"] for r in rows])
    matrix = table(['Task', 'V1', 'V3', 'Steps V1/V3', 'Est. USD V1/V3'], [
        [task, *[verdict(next(r for r in rows if r['task_id'] == task and r['profile'] == p)) for p in ('v1','v3')],
         ' / '.join(str(r['steps']) for r in rows if r['task_id'] == task),
         ' / '.join(fixed(r['estimated_usd'], 9) for r in rows if r['task_id'] == task)] for task in plan['tasks']])
    summary_table = table(['Group', 'Resolved / judged / planned', 'Submitted', 'Steps / average', 'Prompt / cache / completion', 'Est. USD', 'Agent loop / process s', 'Judge process s'], [
        [p, '3 / 3 / 3', '3 / 3', f"{t['steps']} / {t['steps']/3:.2f}",
         f"{t['prompt_tokens']} / {t['cache_hit_tokens']} / {t['completion_tokens']}", fixed(t['estimated_usd'], 9),
         f"{t['agent_wall_seconds']:.3f} / {t['agent_process_wall_seconds']:.3f}", fixed(t['judge_wall_seconds'])] for p,t in totals.items()])
    evidence_table = table(['Task / profile', 'Raw trajectory', 'Full / evaluation patch', 'Official per-task report'], [
        [f"{r['task_id']} / {r['profile']}", relative_link(r['evidence_path'],'JSONL'),
         relative_link(r['result_path'].replace('.result.json','.patch'),'full') + ' / ' + relative_link(r['result_path'].replace('.result.json','.evaluation.patch'),'production-only'),
         relative_link(r['judge_path'],'report.json')] for r in rows])
    command_table = table(['Command label', 'Exit', 'Wall s', 'Raw command + stdout/stderr'], [
        [c['label'], c.get('exit_code','RUNNING'), fixed(c.get('wall_seconds',0)),
         relative_link(f"runs/interview-real-20261009/commands/{c['label']}/command.json",'command.json') + ' / ' +
         relative_link(f"runs/interview-real-20261009/commands/{c['label']}/stdout.log",'stdout') + ' / ' +
         relative_link(f"runs/interview-real-20261009/commands/{c['label']}/stderr.log",'stderr')] for c in data['commands']])
    exact_commands = '\n\n'.join('```bash\n' + shlex.join(commands[label]['command']) + '\n```' for label in (
        'pytest-offline','pytest-v1-golden','pytest-docker-images-ready','agent-help','judge-help',
        'agent-v1-django__django-16429','agent-v3-django__django-16429','judge-v1','judge-v3'))
    baseline_rows = read_json(OUT / 'image_local_baselines.json')
    baselines = table(['Task', 'Dataset base SHA', 'Image HEAD', 'Equal source tree SHA'], [
        [r['task_id'], r['checks'][0]['output'].strip(), r['checks'][2]['output'].strip(), r['checks'][1]['output'].strip()] for r in baseline_rows])
    telemetry = table(['Task / profile', 'Auto pre-repro', 'Auto flip', 'First production edit', 'Search', 'Output truncations'], [
        [r['task_id'] + ' / ' + r['profile'], r['pre_fix_reproduced_telemetry'], r['repro_flipped_telemetry'],
         r['first_production_edit_step'], r['search_calls'], r['truncations']] for r in rows])
    report = f'''# RepoFix V3 真实实验报告

日期：2026-10-09，Asia/Shanghai。性质：已知 DEV 回归／探索性 V1/V3 对照，不是新 Holdout benchmark。

## 结论先行

实际执行 3 道任务 × 2 个 profile × 1 次，共 6 次真实 DeepSeek + Docker。官方 SWE-bench 独立判分：V1 **3/3**，V3 **3/3**；6 次均 submitted，Judge 失败、运行终止错误、未判分均为 0。没有最终失败题，但存在真实工具错误和验证不充分的负面证据，详见案例。没有重跑任何模型任务或人工修补 Agent patch。

V3 多用 {step_delta} 步（+{step_delta/totals['v1']['steps']:.2%}）、{prompt_delta:,} prompt tokens（+{prompt_delta/totals['v1']['prompt_tokens']:.2%}）、估算费用多 ${cost_delta:.9f}（+{cost_delta/totals['v1']['estimated_usd']:.2%}），没有多修好题。不能据此声称 V3 效果提升；也不能据此证明 V3 在未知长任务上无用。

## 1. 环境、版本、计划和安全边界

任务指定的长文件名在根目录不存在；实际读取并执行根目录《真实评测任务书.md》，未修改这份任务书或结果模板。执行入口来自 `v3`，源仓库 HEAD 为 `{plan['source_sha']}`；主分支仍为 `3fe3c0e25a8aa72b2d9e73bee4d995228b9300ac`。

隔离分支：`codex/interview-real-20261009`。WSL 工作目录：`{ROOT}`。6 次运行首行记录的实际 Git SHA 全部为 `{sha}`；相对源版本，只增加评测／传输／报告辅助脚本，Agent 业务实现、Prompt、Tools、tests 和冻结配置未改。报告生成脚本版本：`{manifest['report_generator_git_sha']}`。后续文档提交不改变实验版本。

WSL `/home/jiusi/venvs/repofix/bin/python`，Python 3.12.3；swebench 5.0.2、datasets 5.0.1、openai 3.19.2、docker SDK 7.2.0、pytest 9.1.1。Docker Desktop 4.71.0 / Engine 29.4.1 / API 1.54 / linux-amd64。Git、Python、Docker 均实际在 WSL 执行；Windows curl 仅用于经既有本地代理下载公开镜像字节。

计划在首次模型调用前冻结，时间 `{plan['created']}`。按 tasks.txt 原顺序取前 3 道：16429、15277、13343；为约 3 小时／$3 预算内优先保证独立判分，没有按结果筛题。完整 ID 在下表。每题先 V1 后 V3，各一次。其余 DEV 未运行；HOLDOUT 不运行、不判分、不读取内容；只读 ID 做拒绝运行检查、旧文件名／哈希做保护审计。

API key 仅从本机 gitignored .env / 环境变量读取，报告只记录存在性；不进入 Docker、Judge、命令参数或提交。没有把 gold patch、官方 test patch 或隐藏评测结果送入 Agent；Judge 结束后不再让模型处理判分结果。容器访问公开基线仓库，runtime network=none。Codex 负责评测辅助代码、基础设施排障、执行和证据整理；修复 patch 来自被测 DeepSeek，不是 Codex 代修。

### 配置与仍然存在的差异

共同：deepseek-flash，api.deepseek.com，temperature=0，thinking=disabled，最多 50 步，单任务估算 $0.5 上限（响应后计费，不是请求前硬拦截），provider timeout=300s，SDK max_retries=2，max_completion_tokens=8192，BM25，12,000 字符头尾截断，reproduction-first，production-only evaluation patch。Dense/RRF、Reviewer、subagents 均关闭。未设置随机 seed。

两组初始 bugfix system/user prompt 相同；V3 原生工具集合、read-before-edit、Hooks、Checkpoint、Context、权限检查、后台 shell 等不同。V3 readonly 并行开启但这轮没有观测到并行批次；V3 默认 sandbox hardening 使用 2 CPU / 4GB / drop capabilities，V1 无同样资源限制。因此不是“只改变 Context”的单变量实验。V1 固定先运行，Provider cache／时段顺序可能影响成本和耗时，temperature=0 也不保证重复结果完全一致。

### 镜像基线身份

使用官方 `swebench/sweb.eval.x86_64.django_1776_django-<ID>:latest`。镜像 HEAD 为额外的 SWE-bench commit，与 dataset base SHA 不同，但逐镜像读取本地 Git 对象确认 source tree 完全一致、diff 为空、运行前工作树干净。不是只凭 tag 或 SHA 字符串猜测。

{baselines}

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

{full_table}

所有数字来自 `.result.json`／JSONL／官方 report，CSV 由程序导出，并经独立 CSV 读入审计逐单元格核对；空值不填 0。完整字段见 `results.csv`，每行记录 profile、repeat、配置哈希、SHA、run_id、real_provider/real_docker、Judge、token、机制事件及证据路径。没有删去失败轨迹；本次没有最终失败 run。

### 同题矩阵

{matrix}

V3 独胜=0，V1 独胜=0，两者都失败=0，两者都成功=3。不是 6 道独立题，而是 3 道已知题的两种配置各一次。不能计算或宣称泛化成功率，未报告有意义的 P95 或显著性。

### 汇总、时间和估算费用

{summary_table}

合计 prompt={combined['prompt_tokens']:,}，completion={combined['completion_tokens']:,}，total={combined['prompt_tokens']+combined['completion_tokens']:,} tokens；cache hit={combined['cache_hit_tokens']:,} 是 prompt 的子集，不额外加一次。工具调用={combined['tool_calls']}。

冻结价格：cache hit input $0.006/M、miss input $0.30/M、output $1.20/M，沿用仓库配置及 pricing_source，不声称核对了当天账单。公式 `(prompt-cache)*0.30/1e6 + cache*0.006/1e6 + completion*1.20/1e6`。合计 **${combined['estimated_usd']:.9f} 估算 USD**，不是实际支付凭证。

Agent loop 合计 {combined['agent_wall_seconds']:.3f}s；完整 Agent 进程（含容器/index/setup/cleanup）{combined['agent_process_wall_seconds']:.3f}s；官方 Judge 两批合计 {combined['judge_wall_seconds']:.3f}s；Agent 进程 + Judge={combined['agent_process_wall_seconds']+combined['judge_wall_seconds']:.3f}s。计划冻结到最后 Judge 结束的实验窗口={elapsed:.3f}s，包含基础设施准备/检查，约 {elapsed/60:.2f} 分钟；不包含更早源码审阅或后续写文档时间。

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

{telemetry}

## 6. 失败、阻塞及恢复记录

开始 Docker Desktop 未启动，启动本机已安装服务后可用。首次 Docker 集成测试失败、Docker Hub 拉取 EOF；WSL skopeo 公开镜像复制因 auth.docker.io 路由／认证网络超时失败。使用现有 Windows 本地代理下载官方 manifest/config/layers，逐项 SHA256 校验，再以 WSL docker load 导入。没有更换为未知镜像或修改全局代理/daemon 配置。

首版导入辅助脚本错误地要求 Docker image ID 等于 OCI config digest；Docker 29 containerd 导入后返回转换后的 manifest ID。保留该失败，评测辅助脚本改为检查 rootfs diff_ids、runtime Config、架构/OS，后续确认成功。首次基线检查要求 HEAD SHA 字符相等也不成立；后续本地 Git tree 核对三题均相同。原始失败日志、未完成的外部查询均保留，不写成 Agent 回归。

这些恢复只涉及环境／评测脚本，在首次模型调用之前完成必要导入及检查，事后追加的本地 Git 对象检查为只读。没有改业务实现或通过重跑抹掉失败。BLOCKERS.md 区分已解除阻塞与待修产品问题。

## 7. 实际命令和证据定位

以下为保存的原始 argv；cwd 见各 command.json，Docker pytest 另设置 `REPOFIX_DOCKER_INTEGRATION=1`，模型进程单独加载 key 环境变量，未放 argv。其他四次 Agent 的完整命令也在附表，只有 profile/task/run_id/output-dir 不同。脚本返回码之外还核对 result.status，未发现 runner_error。

{exact_commands}

### 六份轨迹、两种 patch、独立判分

{evidence_table}

Judge 聚合文件分别是 `runs/interview-real-20261009/judge/v1/deepseek-flash.interview-real-20261009-v1.json` 与 v3 同名形式。每题判分目录保留 report.json、run_instance.log、test_output.txt、eval.sh、patch.diff。原始轨迹只保留 runs 内这一份，未复制到旧 dev_artifacts。模型阶段结束后才产生/读取这些判分材料。

### 全部受记录命令，包括失败

{command_table}

## 8. 数据审计和未验证范围

`evidence_manifest.json` 记录配置哈希、任务清单、6 次实际 SHA、原始结果／轨迹／patch／checkpoint／Judge／命令日志及镜像 provenance 的 SHA256；`csv_audit.json` 核对 CSV、原始 usage、公式和官方 resolved 字段。审计包括真实 key 精确匹配、常见 credential pattern、HOLDOUT ID 检查及 checkpoint 内 base64 数据。历史受保护文件名仅出现在 plan 的保护清单，不是模型内容；首次宽扫描命中该清单的记录也保留在 audit-history，不删除痕迹。

审计结果 PASS，source main/v3 HEAD、源工作区状态、FINAL_CONFIG.md、tasks.txt、holdout.txt、dev_artifacts 哈希均未变。源仓库原有 runs 删除状态和未跟踪任务书未处理。仅隔离分支新增评测脚本和文档；没有 push 或合并。原始 runs/logs/cache 按原有 ignore 规则仅本地保留，不加入提交。

收尾离线复测仍为 132 passed、2 skipped、5 subtests passed（9.61s；命令 wall=10.201s），不是另一个模型实验。最终 `git diff --check` 通过，没有名称含 interview 的实验容器遗留。Docker system df：Images 20.09GB（16 images）、Containers 42.29MB、Local Volumes 1.262GB、Build Cache 0B；这是整台 daemon 当前占用，不全归因本轮，没有清理用户其他容器／镜像。

HTML 与口述 Markdown 同源，UTF-8，无外部资源，实际用已安装 Edge 在 offline 模式下分别以 1280×900、390×844 打开；没有页面横向溢出、远程请求为 0。内置 Playwright Chromium 缺失，因此使用已安装 Edge，没有下载新浏览器。检查结果见 html_audit.json，已目视核对截图。CSV 检查见 csv_audit.json。

未验证：真实模型完整断点恢复、复杂长任务摘要质量、真实 Feature、v3-multi 效果、未知仓库泛化、多个随机重复。feature_seed 是占位，没有人工审阅的可信 PR 输入，不执行不可信 PR；P1 明确跳过。DEV 是历史用于开发的样本，模型训练污染也无法排除，资源／工具／cache 顺序不同，不能把差异全部归因某个机制。

建议后续先修旧 testbed Python helper 兼容与 shell pipeline 验证退出码边界，再设计独立任务和重复对照。**本轮只提出建议，不修改 Agent、不重跑刷分、不运行 HOLDOUT。**
'''
    oral = f'''# RepoFix V3：百度 Agent Harness 一面口述补充

真实评测日期：2026-10-09。下面是可直接口述的草稿，时间长度为目标而非录音实测。Codex 参与了实现辅助、这轮实验执行与审计，不能把全部工作说成个人手写完成。

## 30 秒项目介绍

RepoFix 是一个 Coding Agent Harness：模型决定怎么修，Docker 执行工具，最后独立判分。这轮用真实 DeepSeek 比较三道已知 Django 开发题，V1 和 V3 都是三题通过，但 V3 更费步骤和 token。目前证明了链路能运行，也找到了工具缺陷，还不能证明升级提高了修复效果。

## 90 秒技术主线

V1 的核心是一个单循环：把 issue 发给模型，执行原生 tool call，把 observation 回填，直到提交。升级 V3 不是为了堆多个角色，而是把工具执行、上下文、预算、Hook 和恢复状态拆成能单独测试的部件。现在默认仍是单 Agent，子 Agent 是可选能力，并没有在这轮开启。

没有用 LangGraph，是因为目前控制流主要还是这个循环，没有必须引入图编排的需求。自己维护少量明确的状态，反而更容易核对什么时候保存 pending call、什么时候运行工具、什么时候能恢复。不过这不是说不使用框架就一定更好，维护状态和兼容性仍然有成本。

这轮把证据拆成四层：离线单测、真实 Docker 机制测试、真实 DeepSeek 运行、独立 SWE-bench 判分。Docker 中实际检查了大文件编辑、验证失败阻止提交、上下文 artifact 读回和工作区恢复。真实任务里也保存了 checkpoint，但没有做真模型完整中断再续跑；压缩和子 Agent 调用都是零，不能拿这些结果证明长上下文或者多 Agent 有收益。

Agent 的完整 diff 和官方判分 patch 分开保留，后者按冻结规则过滤测试文件。隐藏评测只在 Agent 结束后的 Judge 阶段使用，不回流给模型。这样“模型说修好了”和“官方判定修好了”是两件不同的事。

## 60 秒数据与失败复盘

三道题是 django__django-16429、15277 和 13343，运行前就按开发集顺序锁定，不是看结果挑选。V1 和 V3 官方判分都是三比三。V1 总共 {totals['v1']['steps']} 步、约 {totals['v1']['prompt_tokens']/1000:.1f}K prompt tokens，V3 是 {totals['v3']['steps']} 步、约 {totals['v3']['prompt_tokens']/1000:.1f}K。六次运行合计估算 API 费用约 ${combined['estimated_usd']:.5f}，不是供应商账单。

最有价值的负面案例是 13343。V3 在 Python 3.6 的容器里调用原生查看和编辑工具时报了 is_relative_to 不存在，模型最后改走 bash 才完成。另外，测试输出里出现 FAILED，但经过 tail 管道后退出码是零，Hook 没能据此拦截。官方最终通过不代表这些问题不存在。下一步应该先修工具兼容和验证边界，再做未知任务对照，而不是继续加功能。

## 追问：为什么从 V1 升级 V3？

单循环容易解释，但一旦任务变长，就需要明确工具执行失败后如何记录、输出如何保存、状态如何恢复。V3 是尝试把这些运行责任拆清楚。这个设计动机和效果证据必须分开：本次只验证部分工程能力，没有验证升级能多修 bug。

## 追问：V3 的架构是什么，为什么不用 LangGraph？

核心仍是模型、工具、反馈的单循环，外围由 runtime 管工具，context 管消息和 artifact，checkpoint 管状态与工作区快照，hooks 管事件和验证约束。当前流程没有复杂分支依赖，不需要为了框架而画图。是否改用编排框架，要由后续实际恢复和并发需求决定，不是面试里比名词多少。

## 追问：Context、Checkpoint、Hooks、Subagent 做到了哪一层？

Context 在真实 Docker 中做过受控 masking 和 artifact 读回，但真实模型三题没有触发压缩。Checkpoint 保存和局部恢复验证过，包括 pending call 回填 interrupted 后不自动重执行，不过完整 CLI 与真模型中断恢复还没验证。Hooks 在受控失败测试中会拒绝提交，但真实轨迹暴露了管道退出码掩盖测试失败的边界。Subagent 有实现，默认关闭，本轮调用次数为零，没有多 Agent 效果结论。

## 追问：这次 V3 到底比 V1 好在哪里？

不能说修复效果更好。两者都是三道通过，V3 多 {step_delta} 步、prompt 多约 {prompt_delta/totals['v1']['prompt_tokens']:.1%}、估算费用多约 {cost_delta/totals['v1']['estimated_usd']:.1%}。能展示的是更明确的运行记录与机制证据，同时也要展示它引入的开销和工具兼容问题。三道旧开发题不足以判断复杂长任务上的价值。

## 追问：怎么保证对照公平？DEV 与 Holdout 有什么区别？

两组使用同一版本代码、同一模型和 issue、相同基线源码树、步数和费用预算、同一官方判分器。运行前锁定 tasks.txt 前三题，每题每配置一次，失败也保留，没有人工替模型改 patch。仍有工具、Harness、资源限制和缓存顺序差异，因此不是单变量实验。DEV 是已经用于开发的题，只能做回归探索；Holdout 留给独立验证，这轮完全没有运行或查看其内容。

## 追问：最典型的问题怎么沿轨迹定位？

13343 的 V3 在第 3、7、9 步都报同一个路径 API 错误，结合镜像 Python 3.6 可以定位到工具 helper 的兼容边界。第 10 步模型改用 bash 编辑，第 12 步再调整，第 13 步原复现通过，第 35 步提交。官方 Judge 确认修好，但第 28 步联合测试失败却返回退出码零，所以我会把“任务最终成功”和“运行组件没有缺陷”分开讲。这里的修复是 DeepSeek 自己产生的，不是 Codex 在外面替它补的。

## 追问：如何避免把自动 telemetry 当成事实？

15277 的 V1 在第二步捕获了 TypeError 并打印出来，然后脚本正常退出；第三步修复、第四步同脚本输出 clean OK。自动统计却说没有复现。这说明退出码正则只能当辅助，原始脚本、输出与修改顺序才是核查依据。我会保留原始 telemetry，不改数据去配合故事。

## 一页数据卡

{matrix}

V1：3 次真实运行、3 次独立 Judge、3 resolved；V3 同样 3/3。无 NOT_JUDGED 或最终 ERROR，没有“V3 独胜”“V1 独胜”“两者都失败”的题。这个三题小样本不称泛化成功率。

V1 prompt/cache/completion：223632 / 212092 / 6355；V3：336449 / 317312 / 7989。Cache 是 prompt 子集。合计 {combined['prompt_tokens']+combined['completion_tokens']:,} tokens、102 次 SDK 层请求、111 次工具调用；估算 ${combined['estimated_usd']:.9f}。

完整 Agent 进程合计 {combined['agent_process_wall_seconds']:.1f}s，独立 Judge 合计 {combined['judge_wall_seconds']:.1f}s；计划冻结到判分结束约 {elapsed/60:.2f} 分钟，含镜像准备与检查，不含后续文档整理。单题 Judge 时间没有独立记录，不伪造。

本次 pytest：132 passed、2 Docker skips、5 subtests passed；镜像准备完成后的真实 Docker pytest：2 passed；补充真实 Docker 机制检查：4/4。首次镜像不可用导致的 2 个 Docker 测试失败也保留。离线通过不当作模型修复成绩。

## 数据出处与一句话边界

模型运行 SHA：`{sha}`；源 v3：`{plan['source_sha']}`。模型 deepseek-flash，temperature=0，thinking disabled，50 steps，单题估算 $0.5，BM25；Reviewer、Dense 和 subagents 关闭。

原始数据根：`runs/interview-real-20261009/`；逐 run 指标 `docs/interview/20261009-real/results.csv`；官方判分 `logs/run_evaluation/interview-real-20261009-v1/` 与 `interview-real-20261009-v3/`；具体文件 SHA256 在 `evidence_manifest.json`。本 HTML 已内嵌全部口述稿和数据卡，跨设备只需复制这一文件，不依赖这些路径才能阅读。

**能说：真实 DeepSeek + Docker + 独立 Judge 的三题对照跑通，并定位了实际工具和验证问题。不能说：V3 提高了修复成功率、压缩节省了 token、多 Agent 已被验证、领先 Codex/Claude，或具有正式 benchmark 成绩。**

没有真实 Feature／可信 PR 任务、复杂长任务、多次随机重复或完整 Docker CLI Resume 证据；不把历史 V1 成果算到 V3。这轮 Codex 参与实验执行、基础设施排障、数据整理与审计，个人贡献须按实际参与情况补充，不能编造分工。
'''
    blockers = '''# 本次阻塞、恢复与未验证项

最终状态：P0 的 A/B/C/D 全部已执行；没有尚未判分的 run。保留下面所有失败，不运行额外模型任务。

| 项目 | 实际问题 | 处理／证据 | 当前状态 |
| --- | --- | --- | --- |
| Docker daemon | 初始未启动，WSL socket 不存在 | 启动已安装 Docker Desktop；commands/environment | 已解除 |
| 官方镜像拉取 | docker pull EOF，首次 Docker pytest 2 failed；skopeo auth 路由超时 | commands/pull-*、pytest-docker、registry-copy-* | 官方镜像经已有代理下载，逐 blob/hash/rootfs/config 校验后 WSL docker load，后续 2 passed |
| 导入辅助检查 | 把 image ID 误当成 config digest | commands/verified-transfer-python | 只修评测辅助检查，v2 import 验证通过，失败原样保留 |
| 镜像 HEAD | 与 dataset base SHA 不同；GitHub tree 请求超时 | docker_sanity.json 与 image_local_baselines.json | 本地 Git 对象证实三题源码树一致，无差异 |
| V3 Python 3.6 | view/str_replace/apply_patch helper 的 is_relative_to 不可用 | V3 13343 steps 3/7/9 | 未修业务代码；Agent 走 bash，官方 resolved，不重跑 |
| 验证边界 | tail 管道导致 FAILED 输出配 exit 0 | V3 13343 steps 15/28/33；V1 也有类似输出 | 未修；不能声称完整测试全绿 |
| 自动复现统计 | 捕获 TypeError 后 exit 0，telemetry=false | V1 15277 steps 2–4 | 保留原值，人工核查解释 |
| Full CLI Resume | 本次只验证 store/workspace/pending 恢复 | docker-mechanisms/results.json | 未验证真实模型完整 kill/restart |
| Feature / multi | 无正式冻结且人工审阅的真实 PR TaskSpec；seed 为占位 | 没有执行 placeholder / --fake 充数 | P1 未验证，本轮停止 |

全部路径相对隔离工作区，命令和开始/结束/退出码见 `runs/interview-real-20261009/commands/<label>/command.json`，同目录保存 stdout.log、stderr.log。不得重用已有输出目录重新跑模型或 Judge 覆盖证据。

下一条安全命令（只读，无 Provider、无新容器）：

```bash
/home/jiusi/venvs/repofix/bin/python scripts/interview_documents.py --check
```

若未来另行授权修产品缺陷，先从旧 Python helper 和管道验证退出码开始，独立提交、保留修前数据，再另行冻结新实验计划。这不是本轮自动执行项。
'''
    contents = {
        'RepoFix_V3_真实实验报告.md': report,
        'RepoFix_V3_百度一面口述补充.md': oral,
        'RepoFix_V3_百度一面口述补充.html': render_html(oral),
        'BLOCKERS.md': blockers,
    }
    for name, value in contents.items():
        (DOCS / name).write_text(value, encoding='utf-8')
    check()


def check():
    """Read-only validation: no provider, subprocess or secret-value output."""
    manifest = read_json(DOCS / 'evidence_manifest.json')
    for item in manifest['evidence']:
        path = ROOT / item['path']
        assert path.is_file(), item['path']
        assert file_sha(path) == item['sha256'], item['path']
    key = load_key()
    holdout = (ROOT / 'holdout.txt').read_text().split()
    paths = list(DOCS.glob('*'))
    for path in paths:
        if not path.is_file():
            continue
        value = path.read_text(encoding='utf-8-sig')
        assert not key or key not in value, 'secret in document'
        assert not any(task in value for task in holdout), 'holdout identifier in document'
        assert not re.search(r'(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9_]{30,})', value)
    document = (DOCS / 'RepoFix_V3_百度一面口述补充.html').read_text(encoding='utf-8')
    parser = HTMLAudit()
    parser.feed(document)
    assert parser.h1 == 1 and parser.viewport and parser.tables == 1
    assert not parser.external and 'url(' not in document.lower() and '@import' not in document.lower()
    assert all(word in document for word in ('30 秒项目介绍','90 秒技术主线','60 秒数据与失败复盘','不能说'))
    assert len(re.findall(r'完整 Agent 进程', document)) == 1
    print(json.dumps(dict(manifest_hashes_verified=len(manifest['evidence']),
        documents_secret_audit='PASS', html_utf8=True, html_external_dependencies=0,
        html_responsive_viewport=True, passed=True), ensure_ascii=False))


if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        check()
    else:
        main()
