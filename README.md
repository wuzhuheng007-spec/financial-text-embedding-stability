# Financial text embedding stability

This project studies how text processing changes company representations and prepares a controlled test of whether text adds information to financial predictors. The completed results are engineering studies on five fixed SEC filings and one pinned MiniLM model. Formal M5 financial training, prediction evaluation and backtesting have not run.

The [14 November progress report](reports/2026-11-14_daily_progress.md) ([中文简述](reports/2026-11-14_daily_progress_zh.md), Asia/Shanghai) records a saved-vector neighbor diagnostic, the repaired F1–F4 evaluator contract, a bounded real 10-K candidate discovery ledger, and source-bound case review. Five candidates have both a conservatively extracted Item 1 and a finite candidate-ID interval without an unresolved-end hit. Historical issuer-to-security validity and the primary-security rule are still unsupported; the discovery ledger marks zero rows formally admitted, no formal cohort size is set, and financial evaluation remains `NOT_RUN`. The [public evidence map](results/m5_20261114_public/EVIDENCE_MAP.json) binds sanitized outputs to their local source versions.

## News research roadmap / 新闻研究技术路线

The [current news research entry / 新闻研究当前入口](docs/news_research/README.md) prioritizes major news, macro releases, earnings and conditional transmission across six technology issuers. The [15 November source-bound intake report](reports/2026-11-15_earnings_macro_intake.en.md) ([中文简报](reports/2026-11-15_earnings_macro_intake.zh.md)) records six development earnings cards, three official macro cards and six separate issuer news inputs. The [Jin10 MCP development report](reports/2026-11-15_jin10_mcp_intake.en.md) ([中文进度](reports/2026-11-15_jin10_mcp_intake.zh.md)) records a bounded fresh-process connection, incomplete flash pagination, recoverable private storage and a separate guidance-timing repair. The [desktop and browser acceptance report](reports/2026-11-15_jin10_desktop_acceptance.en.md) ([中文报告](reports/2026-11-15_jin10_desktop_acceptance.zh.md)) records one native desktop calendar call and a saved private-page check at desktop and narrow widths. Field-level gaps and source hashes are retained; historical security identity and qualified pre-release consensus remain unresolved, while financial experiments are `NOT_RUN`. The earlier [12 macro/technology candidates](docs/news_research/macro_tech/candidates.en.md) and prior company-news engineering stay documented separately.

The [read-only market observer v1](docs/news_research/market_observer/README.md) ([English progress](reports/2026-11-15_market_observer_v1.en.md), [中文进度](reports/2026-11-15_market_observer_v1.zh.md)) adds a private, recoverable SQLite service and a locally checked dashboard. A scoped moomoo MCP quote probe and replay of previously saved Jin10 news are recorded separately from continuous collection. Live quote push, account-specific quotas and actual pre-window market outcomes remain unverified; no trading or formal financial experiment was run.

Three source-bound [technology event-impact development cards](reports/2026-11-15_event_impact_cards.en.md) ([中文报告](reports/2026-11-15_event_impact_cards.zh.md)) now cover an NVIDIA earnings release, a distinct CoreWeave capital-spending outlook update and an official U.S. CPI release. The [public page](results/news_event_impact_20261115/dashboard_public_v3.html) separates verified facts, conditional `AI_ANALYST_DRAFT` paths and unknowns; a saved-data private version passed desktop and 390px browser checks. The cards are analysis templates awaiting evaluation, not contemporaneous forecasts or evidence of financial validity. No new provider call, model inference or return evaluation ran in this stage.

The subsequent [first bounded Qwen short-evidence run](reports/2026-11-15_news_shortfacts_qwen.en.md) ([中文报告](reports/2026-11-15_news_shortfacts_qwen.zh.md)) made six actual local generations on six frozen official passages. All six produced responses, but only one was strict raw JSON; five fenced responses were parsed as separate views. All six quoted spans matched the input mechanically, while Codex content review found field errors and eight omissions relative to a 14-fact pre-run development reference. The [redacted run receipt](results/news_shortfacts_20261115/run_receipt_redacted.json) and [validation summary](results/news_shortfacts_20261115/public_validation_summary_v2.json) keep format, source matching and content judgments separate. This is development evidence, not a general extraction or financial result.

The [shortfacts v2 report](reports/2026-11-15_news_shortfacts_v2.en.md) ([中文报告](reports/2026-11-15_news_shortfacts_v2.zh.md)) records a corrected entity/metric contract, a new `CODEX_REFERENCE_DRAFT` restricted to the same six passages, retrospective scoring of the untouched v1 responses, and one bounded same-passage retest. The [plan–actual ledger](results/news_shortfacts_v2_20261115/plan_execution.csv), [contract receipt](results/news_shortfacts_v2_20261115/contract_receipt.json) and [new-run score](results/news_shortfacts_v2_20261115/new_run_summary_redacted.json) distinguish this combined engineering change from the original v1 score. All six new responses were strict raw JSON, but only one of 14 corrected-reference facts was fully correct. The six reused tasks remain development material; extraction generalization and financial validity are untested.

后续从 `docs/news_research/README.md` 读取当前版本，再维护中英路线与状态。六段短原文已完成首次真实 Qwen 开发运行，并在 v2 中修正契约、重评旧输出及进行同材料有界复测；严格格式、事实字段与遗漏分别核对。既有 M5 准入和完整金融协议状态不变，也未启动自动采集。

## Current work

The S2 experiment changed body chunk length across 118, 123, 128, 133 and 138 tokens. S3 shifted the internal cuts of a 128-token reference by -10, -5, 0, 5 and 10 tokens while preserving each document's chunk count. Both experiments retained every setting and compared equal-chunk with body-token-weighted pooling using the same chunk vectors. Saved vectors were read back to recompute the descriptive measures. These comparisons do not isolate causal contributions from boundaries, tails or chunk count, and they do not select a preferred pooling method.

Historical M5 data have since been received and inventoried locally: the starting security set, security and issuer histories, CIK-CUSIP observations, eight Daily segments, bounded Distribution data, delisting records and a CCM relation index. Their public reports describe coverage, source identities and unresolved cases. Licensed rows, private candidate indexes and account material remain local. Receipt verification does not establish supplier completeness, historical CIK validity or a formal eligible cohort.

The current company-event proposal uses a retrospective filing-date proxy and requires a dated security relationship and documented primary issue. Preparation decisions are recorded, but the full protocol remains PROPOSED, researcher_accepted=false and NOT_RUN. Formal company and event counts remain null. Unresolved link codes, date conversion, market applicability, action and exit rules, text versions and source availability still affect admission.

## Read the evidence

- [14 November daily progress](reports/2026-11-14_daily_progress.md) and [Chinese summary](reports/2026-11-14_daily_progress_zh.md)
- [Current progress and remaining decisions](reports/2026-11-13_public_research_progress.md)
- [S2 complete descriptive results](reports/2026-10-31_small_chunk_probe_descriptive_results.md) and [machine metrics](results/s2_round4/metrics.json)
- [S3 complete descriptive results](reports/2026-10-31_boundary_shift_probe_descriptive_results.md) and [machine metrics](results/s3_round6/metrics.json)
- [S2/S3 synthesis and financial feasibility](reports/2026-11-01_s2_s3_m5_feasibility.md)
- [M5 admission evidence](docs/M5_DATA_ADMISSION_READINESS_20261112.md), [identity ledger contract](docs/M5_IDENTITY_CANDIDATE_LEDGER.md), and [remaining company-event requirements](docs/M5_COMPANY_PROXY_REMAINING_GATES_20261113.md)
- [Current M5 proposal](configs/m5_protocol_company_proxy_conditional_20261113.json), [protocol draft](docs/M5_PROTOCOL_DRAFT.md), and [earlier round-8 proposal](configs/m5_financial_protocol_round8.json)
- [CCM source review](reports/2026-11-13_m5_ccm_linktype_source_review.md) and [source-definition gaps](docs/M5_CCM_TARGETED_DEFINITION_GAPS_20261113.md)
- [Research execution plan](docs/RESEARCH_EXECUTION_PLAN.md), [task ledger](TASKS.md), and [roadmap](ROADMAP.md)

The earlier M6/M7 lexical and pooling diagnostics and M8/M9 quarterly-text preparation remain documented under `docs/`, `reports/` and `results/`. The [M10 assistant](docs/M10_RAG_ASSISTANT.md) is a separate passage-retrieval engineering demonstration. Its results do not establish financial usefulness or generated-answer quality. The five-company text/label join is ENGINEERING_ONLY.

## Software and local inputs

Python 3.11 is the reference environment. Install dependencies in a separate environment with `python -m pip install -e ".[embed,test]"`. Choose tests explicitly: some historical integration tests construct a real encoder. A repository-wide test run is not an offline-only check.

The reusable runners validate input, configuration, code, tokenizer/model and decision identities before loading a model. See the [small-chunk decision contract](docs/SMALL_CHUNK_DECISION_RECORD.md) and [S3 decision contract](docs/S3_BOUNDARY_SHIFT_DECISION.md). The repository does not include the licensed market exports, original filings, weights, vectors or private acceptance records needed to execute the historical runs.

## Publication scope

This snapshot descends from the existing public sharing history. It does not import the private development branch's intermediate commits. [Source hashes and publication adaptations](PUBLICATION_20261113_SOURCES.json) identify the copied files; [the snapshot manifest](PUBLICATION_20261113_MANIFEST.json) identifies the distributed payload. Older manifests remain dated inventories rather than checksums for the current tree.

Current overview prose was edited with the installed [blader/humanizer](https://github.com/blader/humanizer) skill. Numerical outputs, bound candidate configurations and historical evidence were not rewritten for style. This publication ran no encoder, training, financial scoring or backtest.

No repository license is granted or implied. Third-party data, model and software terms continue to apply. See [sharing scope](SHARING_SCOPE.md).

