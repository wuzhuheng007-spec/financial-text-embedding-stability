# 研究平台 BACKLOG（M1/M2 之后，均未授权施工）

| 项 | 依赖 | 说明 |
|---|---|---|
| 生产部署 M1/M2 候选 | 审查 diff、迁移和回滚；明确部署指令 | 见 M1M2_DEPLOY_ROLLBACK.md |
| 服务器端 phase5 输入快照 | 所有者把私有 SQLite（c5b5ec22）放到服务器私有路径 | 在此之前 phase5 平台任务在生产上为 BLOCKED_INPUTS |
| 镜像 registry digest | 选定私有 registry 并获授权 | 目前只有本地 image id，tag 不代表 digest |
| 新闻修复上线 | 部署 news 镜像并重启采集器 | 包括水位与续传、陈旧删除和固定副本恢复对账 |
| 前瞻观察 M5 | 冻结新的起点和规则，且须在执行点之前冻结 | 2026-11-17 之后的行情不能自动算作前瞻 |
| M3 因子分析 | TA-Lib parity 烟测；横截面需要历史股票池等证据 | 只保留接口，不抢跑 |
| 文本因子 T1/T2 | 历史新闻面板，需具备可用时间、去重、公司映射和评分版本 | 在线 Benzinga 接通不等于面板合格 |
| 结果页对比视图 | 已有 compare.json | 页面尚未提供并排对比入口，目前只有 API |
| 复制范围文案中文化 | — | grade 中 replication_scope 目前为英文原文 |
