# Macro and technology event contract / 宏观与科技事件契约

Updated 2026-11-15, Asia/Shanghai. The [candidate catalog](../../../results/news_macro_tech_20261115/candidate_events.json) is a development input for relevance screening. It is not an admitted financial event panel.

## Four separate objects / 四个独立对象

| Object | Required content | Boundary |
| --- | --- | --- |
| `FACT_CARD` | Event cluster, dated announcement, two short source-bound facts, exact document locators, publication-time precision and version notes. / 事件簇、日期、两条有定位的事实、时间精度及版本。 | Facts can be agency or issuer statements. A source's assessment is attributed to that source. / 对报告中的判断明确归属发布方。 |
| `EXPECTATION` | Metric, unit, period, actual, prior/revised values, pre-release consensus with source and `as_of` time, if available. / 数值、单位、期间、实际值、前值和修订、带时点的公布前共识。 | `UNKNOWN` stays unknown. `surprise = actual - pre_release_consensus` is calculated by code only after unit and time checks. / 缺少有时点依据的共识时不计算意外度。 |
| `CHANNEL_HYPOTHESES` | At most three candidate paths of at most two edges each; trigger, evidence per edge, contrary scenario and next observable signal. / 至多三条路径、每条至多两跳，附条件、依据、反向情景和下一验证信号。 | Each edge is marked `SOURCE_STATED`, `ANALYST_INFERENCE` or `UNKNOWN`; paths do not establish causality. / 路径不等于已识别因果。 |
| `TECH_EXPOSURES` | Broad market, industry or company target; dated relationship source; direct/indirect and possible sign, with `UNKNOWN` by default. / 大盘、行业或公司对象及有日期依据的关系来源。 | Ticker is only a candidate security identifier; company relation and financial sign require separate verification. / ticker 只是候选证券标识。 |

The public [source evidence index](../../../results/news_macro_tech_20261115/source_evidence_index.csv) maps each catalog fact to the page and locator used in this review. The release or embargo time is stored separately from an independently observed first-public instant. The latter is `UNKNOWN` for all current cards. No article text, private preference export, price label or model response is part of this contract.

公开[来源索引](../../../results/news_macro_tech_20261115/source_evidence_index.csv)逐条连接事实、原件和定位。官方注明的发布/解禁时间与独立观测到的首次公开时刻分别保存；当前全部卡片的后者为 `UNKNOWN`。本契约不包含新闻全文、私人偏好导出、价格标签或模型响应。

## Next gate / 后续门槛

The user first marks relevance in the private choice page. Those selections are preferences, not fact gold. A later reviewer must adjudicate facts, expectations, target identities, direction and each proposed edge. A frozen, rule-based event panel and historical market-data protocol are required before financial labels or evaluation.

用户先在私人点选页标记关注度；这些是偏好，不是事实 gold。下一阶段需由真人分别裁定事实、预期、对象身份、方向和每条假设边。正式金融标签与评价还需冻结的规则化事件面板及历史市场数据协议。
