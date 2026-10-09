# 本次阻塞、恢复与未验证项

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
