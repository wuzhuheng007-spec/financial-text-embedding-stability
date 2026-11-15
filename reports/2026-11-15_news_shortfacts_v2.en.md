# Shortfacts v2: corrected contract and bounded same-passage retest — 15 November 2026

Execution timezone: **Asia/Shanghai**. This development task asks whether the pinned local Qwen model can turn short official evidence into structured numerical facts. The six unchanged passages come from saved [NVIDIA earnings](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027), [CoreWeave earnings and capital-spending guidance](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-Third-Quarter-2026-Results/default.aspx), and the [BLS October 2026 CPI release](https://www.bls.gov/news.release/archives/cpi_11102026.htm), two passages per source. They are reused development material, not a held-out sample. The [baseline readback](../results/news_shortfacts_v2_20261115/baseline_readback.json) verifies the six passage and saved-source hashes; the earlier [redacted input manifest](../results/news_shortfacts_20261115/input_manifest_redacted.json) records source locations and versions.

## Contract and retrospective audit

The [v2 prompt](../scripts/news_shortfacts_v2/prompt.txt) and [Schema](../scripts/news_shortfacts_v2/output.schema.json) were frozen before the new run. `entity` now allows `null` when the measured subject is absent. For the two CPI passages, the corrected private `CODEX_REFERENCE_DRAFT` uses the named measured indexes as subjects; the publisher is source metadata. `metric` describes the numerical measure and distinguishes current from prior outlook; `basis_text` contains only supported accounting, adjustment, or seasonal basis. Values retain ranges, signs, approximations, and tolerances. A predeclared passage-order rule caps targets at three; the six passages contain **3, 1, 2, 3, 2, 3** targets, **14** in total, with no overflow. The [contract receipt](../results/news_shortfacts_v2_20261115/contract_receipt.json) binds the unchanged input, prompt, Schema, and private reference hashes.

A separate [offline diagnosis receipt](../results/news_shortfacts_v2_20261115/offline_diagnosis_receipt.json) records **four prior-reference errors, one input-context gap, one Schema/task conflict, and one unresolved September seasonal-scope issue**. These are design findings, not seven model errors. A private field-by-field table retains the old response hash, raw field, evidence location, decision, cause and reference version. The frozen original v1 reference, raw responses, and [original v1 score](../results/news_shortfacts_20261115/public_validation_summary_v2.json) were not overwritten. The corrected reference is a post-v1 revision and was frozen before this v2 run; it is not independent human gold.

The [retrospective v1 score under the corrected rule](../results/news_shortfacts_v2_20261115/retrospective_summary_redacted.json) used fresh `CODEX_REVIEW` field decisions. Of the six saved v1 responses, **1/6** was strict raw JSON and **5/6** were whole-response fence adaptations; all six views passed v2 Schema. Their six model facts matched six of 14 reference targets one-to-one, leaving eight wholly unreturned. **0/14** reference facts were fully correct under the new rule. The historical v1 score instead reports **0/6 model facts** with every field correct against its original reference; these are different denominators and reference versions. The five adapted views remain raw-format failures.

## Frozen retest and same-rule comparison

The [pre-run freeze receipt](../results/news_shortfacts_v2_20261115/pre_run_freeze_receipt.json) binds the unchanged six passage hashes to one prompt, one Schema, the corrected private reference, the evaluator and the pinned [Qwen/Qwen2.5-3B-Instruct revision](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/tree/aa8e72537993ba99e69dfaafa59ed015b17504d1) `aa8e72537993ba99e69dfaafa59ed015b17504d1`. The existing Python environment and CUDA were reused; no model download, dependency upgrade or constrained decoding occurred. The full prompt lengths were **536–575 tokens**, under the 2,048-token input cap. Batch size 1, greedy decoding, seed 1729, 768 maximum new tokens, 180-second generation and 240-second model-load limits, and zero retries were fixed. [Focused tests](../results/news_shortfacts_v2_20261115/focused_tests.log) passed 16 test functions plus six associated subtests; those are software checks, not 22 independent experiments. Separate [public-receipt](../results/news_shortfacts_v2_20261115/public_receipts_tests_final.log) and [handoff privacy](../results/news_shortfacts_v2_20261115/handoff_tests_final.log) checks passed 5 and 4 tests, respectively, and are not model-quality evidence.

The [redacted run receipt](../results/news_shortfacts_v2_20261115/run_receipt_redacted.json) records **six planned, six attempted, six generation-started and six completed** responses, one call per frozen passage in A1–C2 order, between **11:14:48 and 11:18:42 Asia/Shanghai**. All workers exited normally with EOS; no timeout, OOM, native crash or retry was recorded. Per-task attempts took **30.438–47.516 seconds** and generated **109–290 tokens**. The receipt retains observable per-worker CUDA memory, requested SDPA and actual dtype/device; the effective attention kernel is unknown. Private raw responses and worker logs were preserved without repair.

The [three-way comparison](../results/news_shortfacts_v2_20261115/comparison_redacted.json) and both [retrospective](../results/news_shortfacts_v2_20261115/retrospective_summary_redacted.json) and [new-run](../results/news_shortfacts_v2_20261115/new_run_summary_redacted.json) redacted summaries separate the score versions:

| Version | Calls completed | Strict raw JSON/Schema | Fence-adapted Schema | Output facts | One-to-one reference matches | Fully correct |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Historical v1, original reference/score | 6/6 | 1/6 | 5/6 | 6 | 6/14 | 0/6 output facts had all fields correct |
| Saved v1 outputs, corrected v3 reference and fresh review | No new calls; 6 saved | 1/6 | 5/6 | 6 | 6/14 | 0/14 reference targets |
| New v2 run, same corrected v3 reference/rules | 6/6 | 6/6 | 0/6 | 12 | 12/14 | **1/14** reference targets |

All **12/12** new evidence IDs and quoted spans occur in the frozen inputs mechanically. This is a literal-location check: two quotes omit the value needed for their claimed fact (B1 prior guidance and C2 September), and only **1/12** output facts was judged fully source-supported as stated in the same-system content review; 11 were partial. The single fully correct structured fact is the first A1 revenue fact. Eleven other matched facts had field or quote defects; two reference targets were wholly unreturned (B2 facility amount and C2 August change). No duplicate or extra unmatched output fact was found. An empty facts array did not occur. The corrected-rule **complete-correct recall is 1/14**, which is a development-reference comparison, not a population accuracy estimate.

| Field (`CODEX_REVIEW`) | Saved v1: correct / wrong / unknown, denominator 6 | New v2: correct / wrong / unknown, denominator 12 |
| --- | ---: | ---: |
| Entity | 3 / 3 / 0 | 10 / 2 / 0 |
| Metric | 3 / 3 / 0 | 2 / 10 / 0 |
| Value text | 5 / 1 / 0 | 10 / 2 / 0 |
| Unit text | 4 / 2 / 0 | 7 / 5 / 0 |
| Period text | 3 / 3 / 0 | 6 / 6 / 0 |
| Basis text | 5 / 1 / 0 | 12 / 0 / 0 |

The next engineering priority is **metric/period binding and quote support**, especially keeping actuals, outlooks and financing-program limits distinct. The new run removed fences on these six passages and produced more target matches, but most matched facts still had errors. A next candidate should be defined before any call and evaluated with the same separated gates. The unresolved scope of September seasonal adjustment in C1 should remain explicit rather than silently turned into a model error or a corrected reference fact.

| Stage | Planned action | Actual and evidence | Status |
| --- | --- | --- | --- |
| P0–P1 | Preserve baseline; correct contract/reference offline | [Baseline](../results/news_shortfacts_v2_20261115/baseline_readback.json), [contract](../results/news_shortfacts_v2_20261115/contract_receipt.json), [diagnosis](../results/news_shortfacts_v2_20261115/offline_diagnosis_receipt.json) | Complete |
| P2–P3 | Test exact fence adapter; freeze one configuration | [Retrospective score](../results/news_shortfacts_v2_20261115/retrospective_summary_redacted.json), [pre-run freeze](../results/news_shortfacts_v2_20261115/pre_run_freeze_receipt.json) | Complete |
| P4–P5 | At most six calls; same-rule old/new review | [Run receipt](../results/news_shortfacts_v2_20261115/run_receipt_redacted.json), [comparison](../results/news_shortfacts_v2_20261115/comparison_redacted.json) | Complete |
| P6–P7 | Bilingual documentation; selective publication and handoff | See [full plan–actual ledger](../results/news_shortfacts_v2_20261115/plan_execution.csv); the remote commit is identified in branch history and the handoff README | Complete after remote readback |

## Interpretation and limits

The model, tokenizer, six passage bytes, generation budget, seed and task order were held fixed; the output contract, prompt and corrected reference changed together. Any difference is a **combined engineering revision**, not the isolated causal effect of a prompt sentence. Mechanical JSON, Schema, evidence ID and literal quote checks are separate from same-system `CODEX_REVIEW` semantic judgments. A matching quote does not by itself prove the full fact. This run does not establish general extraction quality, historical forecasting or financial validity. Human gold, formal financial admission, return labels, fitting, prediction and backtesting remain **NOT_RUN**.

The [plan–actual ledger](../results/news_shortfacts_v2_20261115/plan_execution.csv) records each P0–P7 stage and its evidence. Raw passages, original responses, reference rows, weight files and private field judgments remain local under ignored `artifacts/`; public outputs are redacted summaries and reproducible code.
