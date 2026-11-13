# M9 project validation of accepted AMZN and GOOGL Item 2 inputs

**Scope and baseline.** HEAD `03924a65ab1cf4c8430cd872551f18e47adc3244` on `codex/m9-googl-review-20261028` had a clean worktree before this check. The source filings, older Item 2 texts, accepted derivatives, decisions and correction chains were kept unchanged. This is a same Codex agent team self-check of two exact saved inputs. It is not an independent audit, encoder run, multi-company corpus acceptance, or financial experiment. The proposed use is descriptive representation of the saved text, without normalized-date features or historical trading-time claims.

The current official SEC filing-detail pages independently corroborate the accession, filer CIK, 10-Q type and March 31, 2025 report period for [AMZN](https://www.sec.gov/Archives/edgar/data/1018724/000101872425000036/0001018724-25-000036-index.htm) and [GOOGL](https://www.sec.gov/Archives/edgar/data/1652044/000165204425000043/0001652044-25-000043-index.htm). Their listed filing dates are May 2 and April 25, 2025, respectively. This live index check corroborates identity only; it does not prove that the saved local bytes were the first public snapshot or establish a tradable decision time.

## Plan and observed outcome

| Planned check | Actual evidence | Outcome |
| --- | --- | --- |
| Bind each accepted version to its source and decisions | AMZN 12/12 item `ACCEPT` plus overall `ACCEPT`; GOOGL 18/18 plus overall `ACCEPT`; raw, old section, derivative, provenance and event hashes rechecked. The user supplied one Pacific local review time without a year; 2026 is explicitly context-inferred. | PASS for binding; acceptance is distinct from validation. |
| Recheck source and table preservation | AMZN 76 focused source/decision/provenance checks and a 1,956-check saved-derivative readback passed. GOOGL's 3,049-check saved-derivative readback passed; MDA-1/MDA-5 received a separate raw-cell/span check. | Content/provenance `PASS_WITH_LIMITATIONS` for each exact derivative. |
| Check the real preprocessing boundary | Pinned tokenizer and saved-text token/source spans were rechecked at 128 and 192 body tokens without truncation or encoder inference. | Tokenizer-only PASS; current verified M6/quarterly loaders do not select the derivatives. |
| Check comparison-page display evidence | The saved v4 browser receipt hashes the **old** AMZN/GOOGL review pages, not the later comparison pages. The latter have static checks but no actual browser-render verification. | `NOT_VERIFIED` for new-page display. |
| Prepare the next comparison | [Unexecuted draft](../docs/M9_STRUCTURED_SERIALIZATION_SENSITIVITY_DRAFT.md) fixes the old/new within-company inputs and methods. | Prepared only. |

The first attempted rerun of each existing verifier used `--output` as a JSON filename, but that option expects a directory. The failed logs are retained in the new run. After copying only the verifier's required inputs into new subdirectories, the same scripts passed without writing to the original v3 runs. A first consolidated receipt also caught a stale hash while the GOOGL focused output was being regenerated to add unit, blank-cell and caption evidence. The [append-only correction receipt](../artifacts/m9/20261027T235104Z-googl-sparse-header-validation/final_receipt_v2.json) checks the final bytes; the transient earlier bytes were not retained and are not reconstructed. No full model suite was rerun for these documentation and readback changes.

## AMZN: exact accepted structured Item 2

| Object | Frozen reference |
| --- | --- |
| Filing | 10-Q, CIK `0001018724`, accession `0001018724-25-000036`, Part I Item 2 MD&A |
| Raw submission SHA-256 | `834b76e2ff7363205aa7acc8e9219ded61259ecda3debd5b04c79be7388352b5` |
| Original extracted Item 2 SHA-256 | `43015b9006e416f31ff627e52d20bf522e50935c109d269d798641c48c420033` |
| [Accepted structured Item 2](../artifacts/m9/20261027T162744Z-amzn-structured-tables-v3/amzn_item2_structured_text_v1.txt) SHA-256 | `2d9a0eed0b191decee43a6fea9e23ce6af601ce34a44e8eb9aa7a622898bc6d7` |
| [Provenance](../artifacts/m9/20261027T162744Z-amzn-structured-tables-v3/amzn_structured_text_provenance.json) SHA-256 | `80538642393a5dceb707f7f5c677658e1222fef0bfd8526232dafbce09322645` |
| Decision events | [12 item choices](../artifacts/m9/20261027T231528Z-amzn-all-accept-v2/amzn_item_decisions_v2.json), [overall event](../artifacts/m9/20261027T232114Z-amzn-overall-accept-v1/amzn_overall_decision_v1_en.json) |

The [focused validation receipt](../artifacts/m9/20261027T235156Z-project-validation-amzn/amzn_project_validation_en_v2.json) checks SEC header/filing identity, original Item 2 boundaries and Item 3 exclusion, six raw-HTML table byte spans, 118 mapped values, seven unchanged narrative segments, source order, blank spacers, the quarter/trailing-twelve-month and FX basis headings, units, dollar/percent signs, parentheses and two adjacent notes. Its 76 checks passed. The original Item 2 remains a verified primary-text substring. The *structured derivative* deliberately differs from it and must be identified as derived text. Generated labels alter the tokenized input and are not SEC wording. MDA-6 note association is a source-adjacent candidate, not a newly established semantic fact.

**Content/provenance status: `PASS_WITH_LIMITATIONS`. Current-pipeline execution status: `BLOCKED`.** The only permitted immediate use is further read-only inspection and tokenizer diagnostics of this exact saved text. The current M6 `load_sections` rejects a non-substring derivative; a version-bound derivative adapter is required before embedding inference. The accepted decision does not validate first-public time, normalized relative dates, or financial use.

## GOOGL: exact accepted structured Item 2

| Object | Frozen reference |
| --- | --- |
| Filing | 10-Q, CIK `0001652044`, accession `0001652044-25-000043`, Part I Item 2 MD&A |
| Raw submission SHA-256 | `e39849eb31baac03f4d0f0ba254735f20ef039635b1812339aa640c0bfb17730` |
| Original extracted Item 2 SHA-256 | `5e75ca737c479302764d35550f581badd5461183479da2665806ce109a924883` |
| [Accepted structured Item 2](../artifacts/m9/20261027T230835Z-googl-structured-tables-v3/googl_item2_structured_text_v1.txt) SHA-256 | `8d9eb5244c1293e77260a0d8c4031c0d604f1b7294fa88827cb30c63f8c6dc70` |
| [Provenance](../artifacts/m9/20261027T230835Z-googl-structured-tables-v3/googl_structured_text_provenance.json) SHA-256 | `bece9f71053a583992e6576c051d0e368d6edf3d0fa1938e8f8353b230548f75` |
| Decision events | [18 item choices](../artifacts/m9/20261027T231441Z-googl-bulk-accept-v1/review_state_v1_en.json), [overall event](../artifacts/m9/20261027T232123Z-googl-overall-accept-v1/overall_accept_event_v2_en.json) |

The saved-derivative readback verifies fourteen source table spans and unchanged non-table segments. The [focused MDA-1/MDA-5 receipt](../artifacts/m9/20261027T235104Z-googl-sparse-header-validation/googl_sparse_header_validation.json) separately rehashes their raw HTML fragments, 486 source cells, 167 nonempty cell-to-output spans, 31 rows and three adjacent notes. Currency symbols, percentages, parentheses and footnote markers are retained. Blank cells are not each serialized; one label in each table differs in spacing before a superscript footnote. Captions and unit cues are source-linked, but no single unit is asserted for every cell.

**MDA-1 exception.** The source contains sparse `2024`, `2025`, `$ Change` and `% Change` header cells. The derivative records source row, cell ID and physical grid column, and explicitly marks header association pending. It does not assert a verified header-to-value mapping. The association remains `UNRESOLVED_SPARSE_HEADER_TO_VALUE_MAPPING`.

**MDA-5 exception.** The source contains multi-level period, FX, hedging and constant-currency headings with sparse cells. The derivative likewise records physical coordinates and source text without assigning a semantic heading to each numeric value. The association remains `UNRESOLVED_SPARSE_MULTI_LEVEL_HEADER_TO_VALUE_MAPPING`.

**Content/provenance status: `PASS_WITH_LIMITATIONS`. Current-pipeline execution status: `BLOCKED`.** The two unresolved associations do not constitute invented mappings in this exact derivative, so they do not alone block a limited fixed-text descriptive study. They do block reliable numeric period-cell extraction and any claim that those relationships are semantically verified. As with AMZN, the current verified loader does not accept this non-substring derivative; no embedding run is permitted until a provenance-checked adapter is implemented and tested. Historical or financial uses remain unsupported.

## Preprocessing and display limits

The [tokenizer-only receipt](../artifacts/m9/20261027T235100Z-preprocess-validation/preprocess_validation.json) rehashes `configs/smoke.yaml` and the locally pinned tokenizer SHA-256 `be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037` at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. No encoder was loaded. It confirms full body-token coverage, source-span hashes, surviving structural labels and at most 130/194 input tokens including two specials, below the configured 256-token model limit.

| Input | Body tokens | 128-token chunks / tail | 192-token chunks / tail | Mapped label/value fields in disjoint chunks, 128 / 192 |
| --- | ---: | ---: | ---: | ---: |
| AMZN | 10,309 | 81 / 69 | 54 / 133 | 1 / 0 |
| GOOGL | 16,088 | 126 / 88 | 84 / 152 | 2 / 2 |

The split counts apply only to fields with a verified mapping; MDA-1/MDA-5 GOOGL associations cannot be counted as semantic label/value pairs. Chunk separation is a diagnostic limitation, not evidence of truncation or a measured vector effect. The current `m6.load_sections` enforces a raw-derived exact substring; `qpilot.run_representation` selects the older program candidate. Direct `Encoder.chunks(text, ...)` accepts supplied text but is not a verified derivative-aware ingestion path.

The [earlier v4 browser receipt](../artifacts/m9/20261027T011648Z-english-review/browser_check/browser_validation_v4.json) covers AMZN page SHA-256 `7c16fef763e22aac0b901e3eec58e8260f29efa9eb605c65427297cad51cedea` and GOOGL page SHA-256 `46a29ed896ed3e8405565ddf4683d00b1b4422aae21fdc201ae698cf9540ebc9`. The later AMZN and GOOGL comparison pages instead hash to `9558cb0cff0954e5633b406d3f7718d8ed7b7d4f2a2562c035b769333c4b95cd` and `22b7142a6f1d84ab53a8008a49bd958935248d2c13d9e76e69931b783fc3b164`. Their actual rendering is `NOT_VERIFIED` under the recorded local-file browser policy restriction. Static HTML readback supports file identity and source mapping; it cannot establish that a human saw an uncropped comparison. The chat decision events are version-bound, but no browser-form export for the derivative is claimed.

## Remaining company gates and next action

| Company | Current evidence-backed gate |
| --- | --- |
| AAPL | Nine explicit item `ACCEPT` records bound to the saved pilot; no separate overall reply or user-entered review time, and its older browser export/reload gate remains pending. |
| MSFT | Fourteen item `ACCEPT` records (nine direct researcher replies, five delegated agent checks); limited fixed-text `PERIODS` decision recorded. Overall company decision, reviewer-entered time and company gate remain pending. |
| META | Saved source and v4 review page/form exist; no researcher decision record was found in the current M9 evidence, so content and company acceptance remain pending. |

Before any derivative-based inference, implement a fail-closed, version-bound structured-input adapter and focused tests; keep the original exact-substring gate for raw text. Then separately decide whether GOOGL's unresolved header semantics are acceptable for the *specific* fixed-text comparison, and verify the new comparison pages if visual review is required. Do not infer historical availability or financial value from this validation. The machine-readable [project receipt](../artifacts/m9/20261027T235056Z-project-validation/project_validation_receipt.json) and [hash manifest](../artifacts/m9/20261027T235056Z-project-validation/output_hash_manifest.json) hold the checked paths, commands and output hashes.
