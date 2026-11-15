# First bounded Qwen short-evidence fact extraction — 15 November 2026

Time zone: **Asia/Shanghai**. This development run tested whether the pinned local Qwen2.5-3B-Instruct can turn a short official passage into a small fact object. It used six fixed tasks, two each from saved [NVIDIA earnings](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027), [CoreWeave results and capital-spending guidance](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-Third-Quarter-2026-Results/default.aspx), and [BLS CPI](https://www.bls.gov/news.release/archives/cpi_11102026.htm). The [plan–actual ledger](../results/news_shortfacts_20261115/plan_execution.csv) records every stage. These six passages are a **call budget and development material**, not a statistical sample or blind test.

## Frozen input and run

The [redacted input manifest](../results/news_shortfacts_20261115/input_manifest_redacted.json) binds the task order, three saved official source hashes, source locations, extraction methods, passage hashes, and model tokenizer revision. Full passages, raw HTML and a 14-fact pre-run `CODEX_REFERENCE_DRAFT` stay local. The source, passage and reference-quote readback passed before generation. The model saw only the frozen short passage and one fixed [prompt](../scripts/news_shortfacts_v1/prompt.txt); no earlier facts, cards, edges, calculations, reference answers, returns or market consensus entered its prompt.

The [environment readback](../results/news_shortfacts_20261115/environment_receipt.json) matched all 11 local model files against the prior official-snapshot integrity receipt, including the weight shards and tokenizer, for revision `aa8e72537993ba99e69dfaafa59ed015b17504d1`. The [pinned model tree](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/tree/aa8e72537993ba99e69dfaafa59ed015b17504d1) and [research license](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/aa8e72537993ba99e69dfaafa59ed015b17504d1/LICENSE) were checked; this is local noncommercial research use. CUDA was available, with 11,026 MiB free at the environment check; that observation was not a future-memory guarantee. The [redacted run configuration](../results/news_shortfacts_20261115/run_config_redacted.json) fixes one prompt, batch size 1, greedy decoding, seed 1729, float16 on CUDA, requested SDPA attention, 768 output tokens, a 180-second generation limit, and zero retries. The effective attention kernel remains unknown. The fixed tokenizer counted **263–302 tokens for the complete six prompts**, below the 2,048-token input cap; no passage was silently truncated. Focused software checks ran before generation.

The [run receipt](../results/news_shortfacts_20261115/run_receipt_redacted.json) and [redacted attempt ledger](../results/news_shortfacts_20261115/attempt_ledger_redacted.jsonl) record **6 planned, 6 attempted, 6 generation-started and 6 completed responses** in A1, A2, B1, B2, C1, C2 order. Each task made one normal generation call in a disposable worker. No timeout, OOM, native crash, retry, model replacement or dependency install occurred. All six raw responses, worker logs, token counts and a 43-file output-freeze receipt remain in ignored local `artifacts/`; the freeze was checked again after review.

## Separate format, evidence and content checks

The [redacted validation summary](../results/news_shortfacts_20261115/public_validation_summary_v2.json) distinguishes the actual denominators:

| Gate | Observed | Interpretation |
| --- | ---: | --- |
| Strict raw JSON and strict raw Schema | 1/6 and 1/6 | Five responses used Markdown fences, violating the raw-output contract. |
| Separately saved fenced views | 5/5 JSON and Schema valid | The five parsed views are **not** relabelled as strict raw JSON. Together with the one strict response, six objects pass Schema only after this explicit view step. |
| Exact evidence ID and contiguous quote | 6/6 model facts | This is a mechanical match, not proof that every field is correct. |
| Codex semantic review | 1/6 source-supported as a stated numerical proposition; 5/6 partial | This is `CODEX_REVIEW`, not independent human gold. |
| Every field correct | 0/6 model facts | Field errors remain even where a number and quote match. |
| Pre-specified reference coverage | 6/14 matched, 8/14 omitted | The frozen reference is a development draft, not an accuracy benchmark. |

Error labels can overlap on one fact. They include **5 format failures**, **8 missing reference facts**, **5 wrong or underspecified metric labels**, **3 entity**, **3 period**, and **one each value, unit and basis** issue. No unsupported numerical addition or duplicate was found in the six reviewed outputs. Across six reviewed facts, `value_text` was judged correct in five, while metric text was correct in one. Four field decisions remain unresolved: three due to reference/input design and one due to unit interpretation. The source-supported count must not be read as a fully correct structured fact count.

The pre-run reference v1 was **not rewritten**. The [versioned correction receipt](../results/news_shortfacts_20261115/reference_correction_redacted.json) records that v1 incorrectly put guidance state into the accounting/adjustment `basis_text` for A2/B1, and requested the BLS institution in C2 although that frozen passage did not name it. A private v2 records these corrections for future sensitivity; it was **not used to improve the primary run score**. A2/B1 basis and C2 entity decisions affected by this design issue are explicitly `UNRESOLVED` where appropriate.

## Decision and limits

**Next single priority: strict raw JSON formatting.** Five of six otherwise separately parseable responses violated that output contract. A future single prompt candidate should target removal of Markdown fences while holding this frozen input/model/budget fixed, then repeat the same separated checks. The observed omissions and field imprecision remain documented; fixing format alone would not make them correct.

The final [focused software log](../results/news_shortfacts_20261115/focused_tests_release.log) is **12/12 passing**; it tests runner/evaluator risks, not extraction accuracy. There was no new page, news collection, local model download, finance label, fit, prediction, backtest or trading strategy; formal financial experiments are `NOT_RUN`. Historical security identity, point-in-time consensus and formal financial admission remain unresolved. Six development tasks cannot establish general model quality, historical forecasting or financial usefulness.
