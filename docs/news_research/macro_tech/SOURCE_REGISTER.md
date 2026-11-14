# 已核验来源入口 / Verified entry points

检索日期：2026-11-15，Asia/Shanghai。下表是**范围调整阶段**核验的发现入口；随后已另行核验具体官方事件原件。入口本身不证明某次值、共识历史、订阅权限或完整正文。

| 来源 | 已直接打开的URL | 能支持什么 | 尚不能支持什么 |
|---|---|---|---|
| BLS CPI schedule | https://www.bls.gov/schedule/news_release/cpi.htm | CPI官方发布日历入口 | 某次值、交易员共识、某事件超预期 |
| BLS Employment Situation schedule | https://www.bls.gov/schedule/news_release/empsit.htm | 就业报告日历入口 | 调查共识或市场反应 |
| Federal Reserve FOMC calendars | https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm | 决议、纪要和相关材料入口 | 用户假设的加息概率 |
| CME FedWatch | https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool | 概率来自30天联邦基金期货隐含信息 | 此刻某个90%值、历史快照可用性、Fed承诺 |
| SEC EDGAR | https://www.sec.gov/search-filings | 公司申报搜索，按公司/日期等找文件 | 任何具体公司的融资利率或高管事件已发生 |
| FRED real-time periods | https://fred.stlouisfed.org/docs/api/fred/realtime_period.html | 数据会修订，历史版本查询机制 | 某系列的分钟级首次可用性或市场共识 |
| EIA chokepoint entry | https://www.eia.gov/international/analysis/special-topics/World_Oil_Transit_Chokepoints | 能源/航运专题入口；本次只读到导航 | 任何新冲突、实际供应中断或油价涨幅 |

Source scope in English: the table above lists discovery and methodology entry points. The event-specific sources in the next section provide bounded evidence for candidate facts. BLS releases do not supply market consensus; CME probabilities require dated snapshots; historical vintages do not automatically establish intraday availability. Jin10, HF and Korean-market coverage remain product-specific access checks. No account credential, subscription text or licensed news body is published here.

## Specific event sources reviewed / 已核验的具体事件原件

The [candidate catalog](../../../results/news_macro_tech_20261115/candidate_events.json) records 12 development cards in six categories. Its [build receipt](../../../results/news_macro_tech_20261115/catalog_build_receipt.json) binds 24 short fact statements to a document URL and locator; the [row-level source evidence index](../../../results/news_macro_tech_20261115/source_evidence_index.csv) carries support type, source version, retrieval time and time precision. The [bilingual directories](candidates.en.md) show the exact per-event links. These counts describe this bounded review, not the source universe.

| Category and card IDs | Reviewed primary evidence | What it establishes within this review |
| --- | --- | --- |
| Macro M01–M02 | [BLS archived CPI](https://www.bls.gov/news.release/archives/cpi_11102026.htm); [BLS archived Employment Situation](https://www.bls.gov/news.release/archives/empsit_11062026.htm) | Published values, revisions, definitions and printed embargo times. No survey consensus or observed market effect. |
| Monetary P01–P02 | [September FOMC statement](https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm); [October FOMC statement](https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm) | Policy decisions, votes and printed release times. No historical futures-implied probability was retrieved. |
| Technology policy I01–I02 | [BIS May guidance](https://www.bis.gov/media/documents/bis-guidance-may-31-2026.pdf); [DOE October statement](https://www.energy.gov/articles/energy-department-supports-fercs-call-pjm-protect-ratepayers-and-build-more-reliable-power) | BIS clarification of an existing license rule; DOE support for a proposed PJM tariff path. No new blanket ban or final PJM tariff is established. |
| Geopolitics G01–G02 | [UK Prime Minister response](https://www.gov.uk/government/news/pm-response-to-the-peace-deal-between-the-united-states-and-iran-14-june-2026); [US Treasury sanctions release](https://home.treasury.gov/news/press-releases/sb0653/) | Dated official statements. The cited pages do not establish actual Strait reopening, shipment effects, oil-price changes or technology returns. |
| Financial stability F01–F02 | [Federal Reserve stress-test release](https://www.federalreserve.gov/newsevents/pressreleases/bcreg20260624a.htm); [November Financial Stability Report](https://www.federalreserve.gov/publications/files/financial-stability-report-20261113.pdf) | A hypothetical stress-test scenario and the Fed's report estimates/assessment. Neither is a realized loss or financing crisis. |
| Technology chain T01–T02 | [CoreWeave Form 8-K](https://www.sec.gov/Archives/edgar/data/1769628/000176962826000357/crwv-20260807.htm); [CoreWeave IR announcement](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Closes-2-6-Billion-Loan-Facility-Expanding-Financing-Flexibility-for-AI-Infrastructure/default.aspx); [NVIDIA IR results](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Financial-Results-for-Second-Quarter-Fiscal-2027/default.aspx) | Issuer-disclosed financing terms and revenue. Intended loan use is not evidence of actual draw or GPU deployment; the SOFR margin is not an all-in rate. |

Printed release/embargo clock times are kept separate from an independently observed first-public instant, which is **UNKNOWN for all 12 cards**. Dated pre-release market consensus is also **UNKNOWN for all 12**. A policy document's later implementation, an issuer's realized performance and any market reaction require separate evidence. A candidate ticker does not verify historical securities identity.

Self-correction: an audit found that the initial G01 June 14 card mixed in a June 17 signing milestone. The June 14 fact set was restricted to what the dated UK statement supported at that time. The later milestone may be researched as a separate dated update, but it cannot be used as a fact available on June 14. The current catalog and source index are the authoritative version; superseded render output remains local only.

## 审查摘要

Final status: SOURCE-BOUND DEVELOPMENT CANDIDATES (12 cards; no admitted financial panel)
Plan–execution gate: P1 source-bound candidate discovery complete; P2 human selection pending
Experimental sufficiency gate: NOT_APPLICABLE (no macro model or financial experiment)
Result provenance gate: PASS for the bounded catalog and source-index linkage
Citation verification gate: PASS within each indexed document and locator; downstream effects unverified
Unresolved items: pre-release consensus, exact first-public instants, dated company exposures, human gold and accepted financial protocol
Claims authorized: source-attributed candidate facts and explicitly conditional hypotheses
Claims not authorized: unverified examples as news, causal transmission, model predictive ability or returns
Worktree/user data preserved: YES
