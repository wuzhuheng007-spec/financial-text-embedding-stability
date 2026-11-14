# Macro and technology transmission / 宏观事件与科技股传导

更新：2026-11-15，Asia/Shanghai（UTC+08:00）。

用户已明确希望关注可能影响大盘和科技股的宏观、政策、地缘、融资与杠杆事件。旧十篇仍保留为 `LEGACY_ENGINEERING_DEV`，旧 LM Format Enforcer token-prefix 门槛失败标记为 `PAUSED_LEGACY_BACKEND`；其失败不阻塞有来源的事件发现。

阅读：README.md → roadmap.zh.md（英文对应roadmap.en.md）→ SOURCE_REGISTER.md → 01_CODEX_TASK.txt。

当前已建立 **12 张有来源定位的开发候选卡**（六类各两张，12 个不同事件簇）和 24 条逐条绑定来源的事实陈述，供用户点选研究兴趣。数量以[目录输入](../../../results/news_macro_tech_20261115/candidate_events.json)和[构建回执](../../../results/news_macro_tech_20261115/catalog_build_receipt.json)为准；检索窗为 2026-05-15 至 2026-11-15。这是事后检索的相关性开发材料，不是规则化准入的金融事件面板。

## 阅读入口 / Start here

| 用途 | 文件 |
| --- | --- |
| 双语候选目录 | [中文](candidates.zh.md) · [English](candidates.en.md) |
| 逐条来源定位 | [来源证据索引](../../../results/news_macro_tech_20261115/source_evidence_index.csv) · [来源登记](SOURCE_REGISTER.md) |
| 研究设计 | [中文路线](roadmap.zh.md) · [English roadmap](roadmap.en.md) · [四对象契约](event_contract.md) |
| 本机点选 | [审阅说明](preference_review_start.md) · [公开 UI 回执](../../../results/news_macro_tech_20261115/preference_ui_receipt.json) |
| 条件式机制示例 | [两条 AI 分析草案](../../../results/news_macro_tech_20261115/analyst_drafts.json) |
| 进度与计划—实际 | [English report](../../../reports/2026-11-15_macro_tech_candidates.en.md) · [中文报告](../../../reports/2026-11-15_macro_tech_candidates.zh.md) · [台账](../../../results/news_macro_tech_20261115/plan_execution.csv) |

本机私人点选入口：在项目本机检出目录中打开 `artifacts/macro_tech_20261115/preference_v1/index.html`；该文件不上传 GitHub。每张卡只需三个点选：是否关注、相关度 1–5 分或跳过、优先对象。无默认选中、无长文本输入；选择导出只留本机。页面初始保存选择 **0/12**，这不是事实 gold。

## 当前证据边界 / Evidence boundary

- 官方原件或公司申报支持每卡的两条简短事实；[来源索引](../../../results/news_macro_tech_20261115/source_evidence_index.csv)保留文件定位、来源声明类型与版本说明。机构评估或公司披露应归属其发布方，不当作已独立证实的后果。
- 12 张卡均没有独立观测到的首次公开时刻，也缺少有日期依据的公布前共识。因此不能计算 surprise、判断是否“超预期”或推断股价方向；官方解禁/发布时刻单独保存，不等于独立观测的首次公开时刻。
- 两条 `AI_ANALYST_DRAFT` 只演示至多两跳的条件式传导路径，不是本地模型推理、人工裁定、因果识别或收益预测。
- 新宏观模型调用 **0**；人工偏好页面初始状态 **0/12**、事实 gold **0**；历史证券身份、市场标签、拟合、预测和收益评价 **NOT_RUN**。完整金融协议仍是 **PROPOSED / 未接受**。

下一步先由用户在私人页点选相关性，再对选中的材料独立裁定事实、时间、预期与目标对象。只有来源时间、连续规则化事件样本、历史证券身份及市场数据协议明确后，才能考虑金融标签与正式评价。用户原先举出的情境仍只是需求例子，不作已发生新闻事实。
