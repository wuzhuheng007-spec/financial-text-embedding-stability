# 历史官方公告语料与新闻特征开发比较

执行日期：**2026-11-15，Asia/Shanghai**。基线为协作分支 `codex/sync-research-progress-20261113` 的 `8f6d0b2dddc5b3c5f4112375e2fd90e83e07772f`；本轮没有重下行情、扩大证券池或启动实时订阅。[逐项计划—实际](../results/market_observer_official_20261115/plan_execution.csv)与[冻结总计划](../configs/market_observer_official_plan_v1.json)记录了预算和口径。仓库内没有 `AGENTS.md`；执行时采用用户直接给出的规则。已有私人数据和无关未跟踪文件均保留。

## 研究目的与实际完成

本轮要判断：在维持旧价格特征和标签定义时，加入有日期依据的**官方公告正文**，是否改变重复使用的验证期开发结果。先核对价格口径，再建立来源范围明确的正文库，最后只对 NVDA 和 MU 的训练、验证期做 A/B/C 共同样本比较。它是回顾性、公告日期代理试验；历史首次可见时间仍未证实。[SEC 的 Submissions API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)是申报索引来源，[美联储会议日历](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)是 FOMC 声明日期和链接来源。公告日期与今天下载日期分别保存。

| 阶段 | 实际结果 | 状态与证据 |
| --- | --- | --- |
| P0 基线与冻结 | 本地和远端均为上述提交；总预算 100 次新请求，SEC 等正文采集上限 80 次；冻结 2025-04-01 至 2026-07-31。 | 完成；[总计划](../configs/market_observer_official_plan_v1.json) |
| P1 价格口径 | 只读核对三只旧试点共 939 条特征行；未发现需要改写旧 B 的差异。 | 完成；[价格审计](../results/market_observer_historical_official_20261115/price_audit_redacted.json) |
| P2 公告正文 | 新增 57 次成功 HTTP 请求，复用 4 件哈希一致的旧原件，形成 60 件正文：39 份 SEC 主申报、10 份识别出的 SEC 财报附件、11 份 FOMC 声明。 | 在指定索引内完成；[覆盖清单](../results/market_observer_official_20261115/corpus_coverage_redacted.json)、[原件检查](../results/market_observer_official_20261115/corpus_validation_redacted.json) |
| P3–P4 对齐与特征 | 单列严格历史可用性与保守公告日期代理；固定 20 交易日回看、按 SEC accession 合并附件、训练期拟合 TF-IDF。 | 开发契约完成；[文本配置](../configs/market_observer_official_text_v1.json) |
| P5 A/B/C | NVDA、MU 各做一次共同样本验证比较；6–7 月已污染测试期未重新评分。 | 回顾性开发完成；[比较回执](../results/market_observer_official_20261115/text_comparison_redacted.json) |
| P6–P7 页面与交付 | 生成[脱敏页面交接 JSON](../results/market_observer_official_20261115/dot_progress_redacted.json)；现有部署绑定和 dot 接口未找到，页面尚未更新。软件检查、来源回读与模型拟合分开记录。 | 页面交接完成，页面更新未运行；同步见本轮提交 |

## 价格时间与复权核对

旧版标签为交易日 *t* 的开盘至收盘方向，六项收盘价和成交量特征最后使用 *t−2*，整天 *t−1* 被跳过。原任务允许在 *t−1* 收盘前判断，但那时 *t−1* 的完整日线尚不可用。本轮比较明确把假设判断点放在 *t−1* 美东时间 09:30，保持旧 B 不变；这不是一条当时真实生成的判断。若未来要使用 *t−1* 收盘数据，须把判断点移至该日线完成且可取得之后，另建版本。

[逐行审计](../results/market_observer_historical_official_20261115/price_audit_redacted.json)对 939 条旧记录重算了特征、标签和交易日。原始与前复权序列在公司行动日明显不同：TQQQ 的拆股会让未复权跨日对数收益出现约 0.693 的机械跳变；[ProShares 公告](https://www.proshares.com/press-releases/proshares-announces-etf-share-splits5)核实其 2:1 拆股于 2025-11-20 开盘前生效。使用未复权日线和截至特征日已登记的行动重建后，与冻结特征的最大差异约 `1.03e-9`；窗口之后的行动因共同乘数在同窗口对数比率中抵消。这个结果支持**保留旧价格基线做相对比较**，不证明供应商行动因子语义已独立核准，也不证明历史日线当时可用。[moomoo 文档](https://open.moomoo.com/api/quote/basic-data/history-kline)说明本轮所用 `autype=0/1` 分别为未复权/前复权。

## 公告覆盖与时间证据

SEC 冻结索引内的 NVDA/MU 8-K、10-Q、10-K 主申报 **39/39** 件均取得正文；按相同 accession 定向识别的财报附件 **10/10** 件取得正文。16 个月 × 两家公司形成的 **32 个 SEC 公司月份**在该限定索引和表格范围内标为完整。独立公司 IR 财报档案的全量性仍为 `UNKNOWN`，不能把 10 份附件当成全部公司公告。FOMC 日期目录中的 **11/11** 份声明取得正文，但可变网页的历史内容版本未确认；它们留在公告库，**不进入本轮 C**。60 件原件和提取文本逐件校验哈希，固定顺序抽查六件身份，10 份附件检查财报/展望线索；原文均只保存在本机。新增请求没有 4xx 或 429。详细逐月“计划、正文、失败、重复、未知”见[覆盖表](../results/market_observer_official_20261115/corpus_coverage_redacted.json)。

五份 SEC 索引记录的 `filingDate` 比接受时间换算到美东的日期晚一天。本轮分别保存原字段，日期代理 *D* 取两者较晚值。其后先等待**严格晚于 D 的首个完整美股常规交易日 F**，从 *F* 后下一交易日的开盘起才允许使用；决策点和真实交易日历均为美东时间。这个规则不会把盘后申报提前用于更早窗口，但**不是历史首次可见时间的证明**。本轮可独立证明的严格历史可用公告仍为 **0**；SEC 归档 URL 与今天的正文哈希只支持回顾性代理。全文版本、发布者、接受时间、公告日期、下载时间和事件组分别保存在私人清单；公开清单仅给出定位和脱敏状态。

## 验证期开发结果

模型输入只取 SEC 主申报及同 accession 的财报附件，不取 Qwen 候选财务数字、金十检索、可变 IR 页面、FOMC 页面或 2026 年 8 月及之后的开发卡。文本做固定清理和长度限制，最近 20 个交易日内已按日期规则可用的公告合并；没有公告只在该 SEC 来源月份覆盖已核实完整时记为零。TF-IDF 词表、IDF、标准化和模型参数只从训练期拟合。A 是训练期多数方向，B 是旧六项价格/成交量特征的逻辑回归，C 在 B 上加公告文本、公告数和覆盖标志。B/C 使用旧参数格 `{0.1, 1, 10}`，在同一训练及验证行上各自选参，所有候选结果保留。[模型配置](../configs/market_observer_official_text_v1.json)与[结果回执](../results/market_observer_official_20261115/text_comparison_redacted.json)绑定输入版本及私人逐行输出哈希。

| 证券 | 共同训练/验证行 | 验证实际下/上 | 验证有/无公告 | A 准确率/平衡准确率 | B 准确率/平衡准确率 | C 准确率/平衡准确率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| NVDA | 229 / 36 | 14 / 22 | 21 / 15 | 61.1% / 50.0% | 38.9% / 38.3% | 41.7% / 48.4% |
| MU | 229 / 23 | 12 / 11 | 5 / 18 | 47.8% / 50.0% | 47.8% / 50.0% | 47.8% / 50.0% |

验证期中与训练期重复同一公告事件组的日期被共同剔除：NVDA 5 日、MU 18 日；[回执](../results/market_observer_official_20261115/text_comparison_redacted.json)列出具体日期与原因。NVDA 的 C 比 B 平衡准确率高约 10.1 个百分点，但低于 A；MU 的 A/B/C 相同。C 同时新增 TF-IDF 和公告数量，故 B→C 差异也**不能单独归因于文本**。模型只在这两只股票、一个来源、一个回顾性日期代理和重复使用的验证集上比较，**不能据此认定公告有稳定增量**。文本长度上限使 18 件事件文档发生截取，3 条合格行的聚合文本也发生截取；这些限制在结果回执单列。6–7 月标签已因上一轮探索污染，本轮不评分、不用于调参，也没有盈利、因果或正式 M5 结论。

[软件测试与结果复核](../results/market_observer_official_20261115/text_comparison_validation_redacted.json)分别于真实下载和真实拟合之外记录，相关软件检查为 15/15 通过。拟合当时未同步记录运行代码哈希；复核回执披露了事后恢复的发布代码版本，因此不能把当前代码哈希冒充运行时已冻结的证据。

## 下一步

核实独立 IR 财报档案与可复现的历史正文版本，寻找真正的历史首次可见记录；继续核实供应商公司行动因子、行情当时可用时点，以及 NVDA/MU 历史公司—证券关系的连续性。若要比较 *t−1* 收盘特征，应另立收盘后决策契约。未来评价需先冻结新的未查看时期，并用更完整且明确获准的新闻来源复验。页面交接仅是脱敏文件，尚未部署或接入 dot。
