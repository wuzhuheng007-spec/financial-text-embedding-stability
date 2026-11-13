# AMZN 10-Q Item 2 structured-input engineering handoff

**Scope:** one saved AMZN original 10-Q, accession `0001018724-25-000036`; source-grid table serialization and researcher comparison only. This is a same-agent self-check, not an independent audit. No encoder inference, batch comparison, prediction, backtest, risk optimization, or push occurred.

## Plan versus actual

| Requested work | Actual outcome | Evidence and limit |
| --- | --- | --- |
| Confirm MDA-1/MDA-6 defects in the exact proposed input | Confirmed in the original saved Item 2 slice, not merely in display HTML. MDA-2 through MDA-5 also joined adjacent year labels. | Frozen source hashes, prior findings, six exact old table spans; no source file edited. |
| Serialize from original HTML, including spans and notes | Six substantive tables serialized using fixed validated source-row profiles. Cell fragments, rowspan/colspan, header/value references, output offsets and SHA-256 mappings saved. MDA-6's two external notes remain unchanged. | `amzn_structured_text_provenance.json`, `amzn_table_content_verification.csv`; two heading/layout tables excluded. Human judgment remains pending. |
| Make a separate representation-input version | Produced `amzn_item2_structured_text_v1.txt` with SHA-256 `2d9a0eed0b191decee43a6fea9e23ce6af601ce34a44e8eb9aa7a622898bc6d7`; parent Item 2 SHA-256 `43015b9006e416f31ff627e52d20bf522e50935c109d269d798641c48c420033`. | Only six table spans replaced; all intervening text and two adjacent notes compare exactly. |
| Check the pinned tokenizer, without running the encoder | The configured tokenizer/model-config/pooling-config hashes matched the original private cache. With truncation disabled, the new text has 10,309 body tokens; 128-token chunks: 81 with 69-token tail; 192-token chunks: 54 with 133-token tail. Two special tokens keep maxima 130/194 under the 256-token limit. | `tokenizer_coverage.json`; original saved Item 2 has 7,130 body tokens under the same tokenizer. Token count change is not a quality result. |
| Test and read back | Focused/regression suite with local saved-source environment: 30 passed. A separate same-agent readback passed 1,956 source, span, table, note, CSV, page-static and chunk checks. | `focused_regression_tests_real_source.log`, `same_agent_readback.json`; an earlier run skipped one real-source test because its environment path was absent, then it passed after the path was supplied. |
| Prepare review comparison and inspect rendering | English old/source/new page, method, content CSV, provenance, memo and blank version-bound form produced. Static page check found six sections, six sanitized source grids, no script/external resources. Browser Use rejected the local `file://` URL and explicitly prohibited a workaround; **actual visual rendering remains unverified**. | `amzn_table_serialization_comparison.html`, `browser_render_check.json`. Researcher must visually inspect locally. |
| Preserve version history and decisions | Old raw/primary/Item 2/index/v4 page and all previous AMZN/MSFT decision records remain untouched. Initial import failure, v1 and v2 outputs remain as correction history; v3 is the current proposed derivative. | AMZN choices 0/12; no user identity, time, or overall decision was inserted. |

## Source and method

The original submission SHA-256 is `834b76e2ff7363205aa7acc8e9219ded61259ecda3debd5b04c79be7388352b5`; embedded primary HTML SHA-256 is `c41a62c1adc4a886085b495f30fbbeafcf85e3499ac607451966567331830172`. The six original table HTML fragments, old flattened Item 2 spans, and new table spans are recorded with source/output coordinates and hashes in the private local run. `scripts/m9_serialize_tables.py` reuses the existing table parser and preserves `<br>` boundaries within source cells. It combines only adjacent source currency/percent marker cells with their numeric cell, leaves blank cells as blank, fails on nested/unknown structures, and never imputes values. Generated words such as `Source row` and `Column` are formatting, not SEC text. The source has inconsistent physical colspan widths across header rows, so ordinal header groups are validated against this filing's exact row order rather than inferred from naive cross-row grid coordinates. See `table_serialization_method.md` for the fixed profile and limitations.

The source captions state `(in millions)` before each table; percentage sections retain their own wording. MDA-1 now separates the three-month and twelve-month year pairs. MDA-6 now identifies As Reported, Exchange Rate Effect (1), and At Prior Year Rates (2) under each year. The two external notes stay in the narrative and are not duplicated inside the table. MDA-3's em dash and parenthetical values remain source-bound. These changes make a proposed input easier to interpret; they do not demonstrate better embeddings or financial usefulness.

## Commands and local run

Current run: `artifacts/m9/20261027T162744Z-amzn-structured-tables-v3/`. The script's `input_output_manifest.json` preserves the complete generation argument vector, source commit, and input/output hashes. The key commands executed from this checkout were:

```powershell
python -X utf8 scripts/m9_serialize_tables.py --ingest artifacts/m8/20261025T230645Z-four-10q/ingest/AMZN --extraction artifacts/m8/20261025T230645Z-four-10q/extraction_v4/AMZN --config configs/smoke.yaml --tokenizer <USER_HOME>/Documents/ChatGPT/quant/.cache/models/all-MiniLM-L6-v2-1110a243/tokenizer.json --model-config <USER_HOME>/Documents/ChatGPT/quant/.cache/models/all-MiniLM-L6-v2-1110a243/config.json --pooling-config <USER_HOME>/Documents/ChatGPT/quant/.cache/models/all-MiniLM-L6-v2-1110a243/sentence_bert_config.json --output artifacts/m9/20261027T162744Z-amzn-structured-tables-v3
python -X utf8 scripts/m9_verify_table_serialization.py --output artifacts/m9/20261027T162744Z-amzn-structured-tables-v3
$env:PYTHONPATH='src;.'; $env:M9_REVIEW_OUTPUT=(Resolve-Path 'artifacts/m9/20261027T011648Z-english-review/review_en_v4').Path; python -m pytest tests/test_m9_table_serialization.py tests/test_m8_review.py tests/test_m8_tenq.py tests/test_m9_english_review.py -q -rs
```

The first attempt failed before artifact creation because `scripts` was absent from the import path; the failure is retained under `20261027T162524Z-amzn-structured-tables-attempt0/`. The next two successful versions remain local, with v3 adding explicit mapping offset bases, original-raw byte comparison, adjacent-note checks and a fuller content CSV. Their structured-text hashes are equal, but only v3 has the final audit metadata. No old result was overwritten.

## Decision and remaining limits

The same-agent memo **recommends**, but does not record, limited descriptive-use acceptance of the new structured text after direct researcher comparison. All twelve AMZN items remain `PENDING_HUMAN_REVIEW`. The changed `MDA-1`–`MDA-6`, `PERIODS`, and exact-input `body` items need new-version review. The unchanged start/end and two heading/layout boundaries are still pending first-time AMZN review; the old v4 form cannot approve the new text by hash transfer. The comparison page's actual browser rendering is unverified. Historical first-public/tradable time, absolute-date feature construction, financial tasks and M5 formal parameters remain unresolved.

**Next minimum task:** the researcher opens the new comparison locally, inspects MDA-1 and MDA-6 with their source grids and MDA-6 notes, then gives explicit `ACCEPT`, `REVISE`, or `REJECT` choices bound to the new input hash; the remaining table/body/period and boundary items must also be decided before company-level acceptance.
