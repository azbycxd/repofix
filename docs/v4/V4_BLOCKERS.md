# V4 阻塞 / 未验证边界

Provider / Docker daemon / 三个 DEV 镜像 / dataset / 官方 Judge：**none**。
本轮三次真实 DeepSeek 和三个官方 Judge 已完成，没有用 Fake 代替。

## BLOCKED_LOCAL_STRUCTURED_VALIDATION（真实环境能力缺口）

三个官方 Django 镜像均没有 pytest；V4 当前只支持 pytest 的结构化本地验证。
实际 run_tests 返回 `ENV_ERROR: pytest/runtime unavailable` / INCONCLUSIVE，未获得本地 PASS。
原生 Django tests 可以由 bash 运行，但其文字输出不能代替可信结构化报告。
安装请求在 evaluation 权限规则下被拒绝，离线缓存也没有可用安装包。

可独立复核环境（不调用模型、不重跑任务）：

```bash
docker run --rm --network none --entrypoint /opt/miniconda3/envs/testbed/bin/python \
  swebench/sweb.eval.x86_64.django_1776_django-16429:latest -m pytest --version
```

该命令预期仍会显示缺少 pytest，不是修复命令。本次 **不** 安装依赖改镜像、降低 PASS 标准、
加 runner 或重新跑 Agent。需要另一个明确任务决定是否支持可信结构化 Django runner。

## NOT VERIFIED

- 恶意仓库/pytest 插件伪造报告、恶意并发修改快照、断电/跨主机恢复。
- 硬实时取消 SDK 线程、Docker daemon 故障后的自动恢复。
- 未见题泛化、功能任务质量、子 Agent/Context/BM25 的因果收益。
- 真实供应商账单与 SDK 内部重试次数（只有响应 usage 估算）。

默认离线测试跳过的 26 个 Docker 用例已在显式 Docker 回归及补充 packaging 测试中执行。
跳过日志仍保留，不能把原 skipped 改写成 passed。
