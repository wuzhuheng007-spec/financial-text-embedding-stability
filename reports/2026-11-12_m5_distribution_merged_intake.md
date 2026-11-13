# M5 merged distribution intake and seven daily-index comparisons

**Scope:** ENGINEERING_ONLY. One merged local distribution extract covers
2020-01-01 through 2026-02-28. This report reads the saved safe summaries;
private rows and identifiers remain in ignored local artifacts. M5 remains
PROPOSED, researcher_accepted=false, NOT_RUN. No new WRDS request, Encoder,
return label, financial training, prediction evaluation, or backtest was run.

## Plan, actual, evidence, limits, status

| Plan | Actual | Evidence | Limit | Status |
| --- | --- | --- | --- | --- |
| Bind one merged input | The handoff and nested ZIP passed transport checks. The bound upload TXT retained 7,639 unique identifiers, identical bytes, order, and set. | Handoff SHA-256 `88433072a22f1f9635d1d5d962d257bc12e8c8d6575e4a5b6a4c47271034b67d`; nested ZIP SHA-256 `7243e04cfa20557caefe5d2976a4bb78ae0d35026d1ca031599b992fc959de7c`; bound TXT SHA-256 `017224e43461409fa6f2ef007d182a5b0d870f02cd1edc4c528f0a901a543318`. | Local TXT identity does not bind the server Input DAT. | LOCAL_INPUT_BINDING_PASS |
| Audit actual rows and fields | The CSV has 129,215 data rows, matching the result page, and all 22 selected columns with no extra or missing columns, bad-width rows, blank physical records, or out-of-window records. Observed `DisExDt` spans 2020-01-02 through 2026-02-27 across 1,548 dates. | Saved `summary_safe.json` SHA-256 `ebd24c590ed63b5ea4b9d2b02ea63364be6b53fc173ec7d23dff70a7a56bdf91`; source CSV SHA-256 `e4116783a981161f6bd2da06e9acd3f682cb0d6fdd5eb6da3cb71e56285306f1`. | A matching page count does not certify provider-universe completeness. | PASS_STRUCTURE_ONLY |
| Review event keys | 6,270 bound identifiers have a row and 1,369 have none in this extract. There are 1,756 same-identifier, same-ex-date groups with multiple sequence numbers. No repeated candidate `(PERMNO, DisExDt, DisSeqNbr)` key or uncertain sequence was recorded. | Saved full-period and annual summaries; audit code SHA-256 `89eeb259889b762d1bcee825598f475c271b87852549b668c1179a2cee51d221`. | Candidate-key uniqueness in this extract is not certification of the supplier's global primary key or formal M5 admission. | CANDIDATE_KEYS_UNIQUE_IN_SAVED_EXTRACT |
| Reconcile seven daily duplicate indexes | All seven local daily index comparisons completed. The daily indexes contain 1,755 duplicate-key groups; all 1,755 intersect distribution multi-sequence groups and match both raw-row and distinct-sequence multiplicity. One additional distribution multi-sequence group appears only on the distribution side, in 2020. | Saved `daily_comparison_safe.json` SHA-256 `c9266b58b85a57bedbb21896810bce6d05bd8e587df7bebb4b4fb309c28e177c`; bound index hashes and exact aggregate fields are in that file. | There is no complete denominator of nonduplicate daily keys. The one distribution-only group is retained for review. Even perfect overlap cannot establish a backend JOIN or causal root cause. | SEVEN_INDEX_COMPARISON_COMPLETE; ROOT_CAUSE_UNKNOWN |
| Reuse first 2019 receipt and verify output | The saved first segment contributes 5,670 rows. The two nonoverlapping collection windows total 134,885 rows; their distinct identifier union is 6,303, not the sum of annual counts. The merged run performed internal saved verification; a separate `verify-output` also exited 0. | First-segment summary SHA-256 `17f5512d34588d525ac6e4b686f62f3545bf728ba67572b9f3f65845ba757685`; merged annual safe summary SHA-256 `3c6cd8a1756dba562d97be54d0ecd816e4338913bc58378a5c1c9a142877ab4a`. | The first-segment CSV was not rescanned by this merged intake. The full-period count combines two acquisition windows, not one provider query. | SAVED_OUTPUT_VERIFIED |

## Seven requested comparison windows

All figures in this table come from saved `annual_summary_safe.json` and
`daily_comparison_safe.json` with the hashes above. The identifier column is
distinct **within each window** and must not be summed across years.

| Window | Distribution rows | Identifiers with a row | Dates | Multi-sequence distribution groups | Daily duplicate groups | Intersection | Distribution-only | Multiplicity matches | Uncertain intersection |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2020 | 22,763 | 4,882 | 254 | 326 | 325 | 325 | 1 | 325 | 0 |
| 2021 | 21,874 | 4,432 | 252 | 404 | 404 | 404 | 0 | 404 | 0 |
| 2022 | 21,381 | 4,491 | 251 | 301 | 301 | 301 | 0 | 301 | 0 |
| 2023 | 21,111 | 4,425 | 250 | 260 | 260 | 260 | 0 | 260 | 0 |
| 2024 | 20,299 | 4,213 | 252 | 221 | 221 | 221 | 0 | 221 | 0 |
| 2025 | 19,747 | 4,084 | 250 | 225 | 225 | 225 | 0 | 225 | 0 |
| 2026 Jan–Feb | 2,040 | 1,546 | 39 | 19 | 19 | 19 | 0 | 19 | 0 |
| **Sum of disjoint windows** | **129,215** | **Not additive** | **1,548** | **1,756** | **1,755** | **1,755** | **1** | **1,755** | **0** |

There are zero daily-only duplicate groups and zero recorded multiplicity
mismatches within these seven comparisons. The distribution-only 2020 key is
an unresolved discrepancy in the **duplicate-group comparison**; the absence
of a complete nonduplicate daily-key denominator prevents calling it a false
positive or an absent daily price record. The earlier first-segment 240/240
relationship is preserved separately; it is not copied into this merged
extract's counts. Repetition of the relationship across seven windows is
descriptive evidence about these saved queries, not proof of the WRDS backend
JOIN or a general company-action mechanism.
The daily indexes retain 1,796 extra duplicate rows across the seven windows
(335, 410, 308, 268, 227, 229, and 19 respectively); these are not
additional independent event observations.

## Source and field boundaries

The user-supplied result page reports Query 11901007, `crsp_q_stock`, Success,
129,215 rows, rounded size 1.8 MiB, and server-displayed elapsed time 14
seconds; its displayed timezone is UNKNOWN. The expanded user parameter text
names 19 distribution fields and three security Header fields among the 22
selected variables, with an explicit ten-code `DisType` predicate and an
uploaded PERMNO selector. `source_saved_query=7991527` is not the result
Query ID. An unused annual-Header lookup entry in the UI metadata is not
evidence that an annual table participated in the executed query. UI index
order is not treated as CSV column order.

The CSV contains eight observed literal `DisType` values, including nine
`N/A` rows; `IN` and `TSOO` are selected by the predicate but unobserved in
this bounded extract. `N/A` was retained as a category, not parsed as
missing. The other event-date fields can fall outside the `DisExDt` query window. Their
outside-window counts are in the machine summary and may overlap individual
rows. Original related-identifier strings and event values were preserved;
no missing values, amounts, prices, factors, or returns were filled or
aggregated into financial labels.

The native Download SQL, this query's server Input DAT, exact CRSP release,
project license/export scope, and provider-universe completeness are still
UNKNOWN or NOT_VERIFIED. The visible SQL button and pasted parameters do not
establish backend JOIN type or complete execution predicates. The saved
daily indexes provide duplicate-key denominators only; they do not establish
formal M5 observations, security eligibility, historical CIK validity, or
return/exit handling. Prior private-index output-control history remains
documented in the previous handoff and is not erased by this run.

## Execution record

The verified baseline was branch `codex/research-plan-round10-20261106`, HEAD
`3983c9a16f506194210e2296a52679d5a38bad9e`, clean. The fixed merged
auditor code commit is `ed5c74544eb36cc1d2e32607988c5ab3c1160626`;
Python 3.11 used the repository's `src` on `PYTHONPATH`, without constructing
an Encoder. Actual focused commands and results:

| Command | Result |
| --- | --- |
| `python -m pytest -q tests/test_audit_m5_distribution_multiyear.py` | Six targeted synthetic tests passed in 1.54 seconds; exit 0. The initial four-test run also passed, then two test-only cases were added. |
| `python scripts/audit_m5_distribution_multiyear.py run --handoff-zip <bound local ZIP> --output-dir <new ignored directory>` | Fixed status `M5_DISTRIBUTION_MERGED_SAVED_STRUCTURE_PASS_SEVEN_INDEX_COMPLETE`; exit 0. |
| `python scripts/audit_m5_distribution_multiyear.py verify-output --output-dir <same directory>` | Fixed status `M5_DISTRIBUTION_MERGED_SAVED_OUTPUT_VERIFIED_STRUCTURE_PASS_SEVEN_INDEX_COMPLETE`; exit 0. |

The run ledger measures 18.98 seconds from 2026-11-12T08:43:47.292931+00:00
to 08:44:06.272166+00:00, including the run's internal verification. The
separate verification command took 11.19 seconds at the tool boundary; peak
memory was not measured and is UNKNOWN. These times are local data-engineering
measurements, not server or model runtimes. Private files and original data
remain Git-ignored. No old first-segment result was overwritten, no WRDS
query was submitted, and M5 remains unfrozen.
