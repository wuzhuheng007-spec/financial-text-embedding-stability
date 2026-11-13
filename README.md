# Financial text embedding stability

This project studies how text processing changes company representations and prepares a controlled test of whether text adds information to financial predictors. The completed results are engineering studies on five fixed SEC filings and one pinned MiniLM model. Formal M5 financial training, prediction evaluation and backtesting have not run.

The [14 November progress report](reports/2026-11-14_daily_progress.md) ([中文简述](reports/2026-11-14_daily_progress_zh.md), Asia/Shanghai) records a saved-vector neighbor diagnostic, the repaired F1–F4 evaluator contract, a bounded real 10-K candidate discovery ledger, and source-bound case review. Five candidates have both a conservatively extracted Item 1 and a finite candidate-ID interval without an unresolved-end hit. Historical issuer-to-security validity and the primary-security rule are still unsupported; the discovery ledger marks zero rows formally admitted, no formal cohort size is set, and financial evaluation remains `NOT_RUN`. The [public evidence map](results/m5_20261114_public/EVIDENCE_MAP.json) binds sanitized outputs to their local source versions.

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
