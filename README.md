## V1 经验与 V2 设计约束

1. 多角色和固定阶段常使真实任务在修改代码前终止；V2 改为单 Agent loop。
2. 自定义 JSON / Schema 链多次导致结构化输出失败；V2 优先使用原生 Tool Calling。
3. V1 的 SourceEvidence → PatchReadyEvidence 严格交接在两个 unseen task 上重复阻塞；V2 不设额外 Evidence Gate。
4. V1 编辑接口经历过 full-file / line-edit / no-op 问题；V2 后续采用更小、更明确、可验证的编辑接口。
5. V1 在 durable runtime / checkpoint / ledger 上投入过多；V2 先证明修 bug 能力，只保留必要轨迹和沙箱。
