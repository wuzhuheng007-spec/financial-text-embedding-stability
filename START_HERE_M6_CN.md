# M6 本轮交付与下一步

本包基于 GitHub 项目 2026-10-19 的分享版本，在独立目录中增加 M6 工程功能。原 Windows 项目、旧结果及远端 main 未被覆盖。

## 已完成

- BM25 词法评分，加入 b=0（不作长度校正）、0.75、1 的工程对照；另行输出 BM25 词项权重向量的余弦矩阵，二者不混淆。
- 原有块聚合保持默认行为，新增按有效正文 token 数加权；比较时仍使用原先固定版本 MiniLM。
- 原始 10-K/10-Q 类型解析与人工审阅章节输入适配；10-Q 的 Item 1 不被当成 Business。没有声称实现自动 10-Q 下载与章节提取。
- 路线、M5 草案附录、M6 运行说明、测试和验收回执。
- 最终本地测试 40 passed、2 skipped。跳过的两项需要未下载的固定模型文件。所有合成文本和模拟编码器只用于软件测试，没有当成真实公司研究结果。

## 尚未完成

真实 M6 BM25 语料分析、真实 dense 聚合比较及正式金融实验均未运行。本包没有原始 SEC 文本和模型权重；不能用现有相似度表倒推出原文，或用合成文本替代原文。M5 科学参数仍未冻结。

发布状态补记（2026-10-25）：下句记录原 ZIP 于 2026-10-24 生成时的阻塞状态；用户随后明确授权将通过检查的 M6 代码与文档上传现有 public 仓库。当前公开审查分支和 PR 的实际状态以 GitHub 为准；`main` 不因准备本包而覆盖。原阻塞记录：远端上传当时被自动审批拒绝，只创建了指向原提交的分支，未创建 M6 commit 或 PR。本包未执行任何可见性变更。发布复核见 `reports/2026-10-25_M6_publication_review.md`。

## 建议阅读顺序

1. `docs/M6_METHOD.md`：调整后的路线、方法、数据格式与运行命令。
2. `reports/2026-10-24_M6_engineering.md`：计划与实际、自检范围、未支持的结论。
3. `reports/2026-10-24_M6_checks.json`：实际检查命令和输出。
4. `docs/M5_PROTOCOL_DRAFT.md`：仍待导师决定的科学参数。

## 原 Windows 项目怎样接入

先保留原项目备份，或在 Git 新分支整合本包改动；不要覆盖原始 artifacts、旧配置与结果。代码改动集中在 `src/finembed/lexical.py`、`src/finembed/m6.py`、`src/finembed/model.py`、`src/finembed/extract.py`、`src/finembed/sec.py`，测试在 `tests/test_m6.py`。

在具有真实 M1 语料和运行环境的项目根目录执行：

```powershell
python -m finembed.m6 --config configs/smoke.yaml --corpus artifacts/runs/20261019T003627Z-d3043c3e/corpus_index.json --output artifacts/m6/lexical-001
```

确认固定模型文件和依赖在本地后，再执行：

```powershell
python -m finembed.m6 --config configs/smoke.yaml --corpus artifacts/runs/20261019T003627Z-d3043c3e/corpus_index.json --dense --output artifacts/m6/dense-001
```

输出目录必须不存在。缺少文件会留下 BLOCKED/FAILED 回执，不应报告完成。若语料路径改变，创建新索引并记录新哈希，保留旧索引；不要为了通过检查修改旧证据。10-Q 必须另建正确章节、经过审阅和哈希检查的索引，格式见方法文档。

## 结论边界

BM25 是词法方法，不是神经 dense embedding。L2 归一化不等于消除文档长度影响；最终向量除以文档长度再做 L2，会抵消这个标量变化。token 加权也不是必然更好。现有工作不支持“提高了预测收益”“已排除前视偏差”或“通过独立审计”等结论。
