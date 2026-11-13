# Distribution SQL source review for two bounded extracts

**Scope:** ENGINEERING_ONLY_SOURCE_REVIEW. The two user-supplied `.sql` files
were inspected offline as text. They were not executed. Their static contents
can be compared with the saved parameter transcripts, upload TXT and CSV
headers; file names and matching content do not attest where or when the files
were downloaded. M5 remains PROPOSED, researcher_accepted=false, NOT_RUN.

## Plan, actual, evidence, limits, status

| Plan | Actual | Evidence | Limit | Status |
| --- | --- | --- | --- | --- |
| Bind the new files | Both named files exist and were hashed; the prior safe result package and saved distribution receipts retained their expected identities. | The exact file hashes and byte counts are below; saved `source_matrix_safe.json` SHA-256 `30f46b6df14327c848ccd79ce3f7905b28adaa5bdf48be6a3b93152ec25f804f`. | No `SOURCE_NOTE` or native download-origin record was provided. | LOCAL_FILE_IDENTITY_BOUND |
| Inspect SQL structure | Both files match the reviewed exact static statement shape: 22 selected fields, 19 from `crsp_q_stock.stkdistributions` and three from `crsp_q_stock.stksecurityinfohdr`; an inner join to a Header subquery on `stkdistributions.permno = id_table.permno`; inclusive `DisExDt` dates and ten literal `DisType` codes. | Saved matrix `sql_structure` and `cross_query` fields; review code SHA-256 `3769bc9d2d62b7060be947a4eaed1b87cac639345fffb61afefd59eb4fd11b0c`. | This is static content review, not a server execution trace or a general SQL parser for arbitrary statements. | EXACT_STATIC_TEMPLATE_REVIEWED |
| Check list and parameter consistency | Each SQL contains 7,639 unique inline Header `PERMNO` literals. Their order and set match the same bound local TXT. The 22 field names, date tokens and ten type codes are present in the corresponding saved user parameter transcript. | Bound TXT SHA-256 `017224e43461409fa6f2ef007d182a5b0d870f02cd1edc4c528f0a901a543318`; matrix comparison booleans. | No server Input DAT was provided; the user-pasted parameter text is not native server parameter evidence. | LOCAL_SQL_LIST_AND_TRANSCRIPT_CONSISTENT |
| Reassess provenance and the prior single-group result | The prior one-group diagnosis remains two distribution rows with distinct sequences and zero rows for that security-date key in the bound saved 2020 daily extract. These Distribution SQL files do not establish the earlier Daily query's join or explain that absence. | [Single-group report](2026-11-12_m5_distribution_2020_single_group_review.md) and unchanged safe result package SHA-256 `07310ddfd701eef06d02cdf24f3435b31a1b7ed97366decb13266097aa60b81e`. | The exact provider release, license/export scope, supplier completeness, historical execution and Daily backend SQL remain unverified. | CAUSE_UNKNOWN; FORMAL_M5_NOT_ADMITTED |
| Save and verify this review | The offline source-review command and a separate saved-output verification exited 0; ten focused synthetic tests passed. | Validation record below and the byte-identical safe matrix in the sanitized handoff. | Peak memory was not measured. No SQL, Encoder, label, fit, score or backtest was run. | SAVED_SOURCE_REVIEW_VERIFIED |

## Query-specific source matrix

| Prior page association | User-supplied file | SHA-256 | Bytes | Static date predicate | SQL/parameter relationship | Open provenance |
| --- | --- | --- | ---: | --- | --- | --- |
| Query 11900944, provisional | `asu1cjbn71x8ajp0.sql` | `2da4d8810b24c357f5c93e63c99eba1d0b33fef544208614555aa0bacdb81cab` | 131,348 | `stkdistributions.disexdt` inclusive 2019-11-01 to 2019-12-31 | Selected fields, uploaded-list identity and ten type literals agree with the bounded saved inputs. | No embedded Query ID, source note, server DAT or execution log. |
| Query 11901007, provisional | `zgt8d3w8jqfnulor.sql` | `7b38c8292ab24f2386767c20331c1168d250f3b1108f3a17f99e060660d98756` | 131,348 | `stkdistributions.disexdt` inclusive 2020-01-01 to 2026-02-28 | The same selected fields, inline-list order and ten type literals agree with the bounded saved inputs. | No embedded Query ID, source note, server DAT or execution log. |

The two statement bodies are identical after replacing the two date literals.
Each uses `crsp_q_stock.stkdistributions` and
`crsp_q_stock.stksecurityinfohdr`; the Header subquery filters its `permno`
with inline literals. The explicit type predicate is `CD`, `CG`, `CP`, `FRS`,
`IN`, literal `N/A`, `ROC`, `SD`, `SP`, and `TSOO`. The reviewed exact shape has
no additional clauses or `DISTINCT`, `GROUP BY`, `ORDER BY`, or `LIMIT`.
These statements are **not** evidence of a Daily query join. A UI lookup for
an annual Header table is not a selected table in these two reviewed files.
In this exact shape, the Header subquery has no `DISTINCT`, `GROUP BY`, or
effective-date predicate. If it contains multiple records for a `PERMNO`,
the `PERMNO`-only inner join could multiply distribution rows. The actual
Header cardinality was not measured; this conditional possibility neither
establishes the Daily duplicate-key root cause nor explains the missing
security-date pair in the separate Daily extract.

The prior user-provided result pages identify Query 11900944 and Query
11901007, but neither SQL text embeds its Query ID. The association of each
local file with its page is therefore provisional, based on file stem, scope,
fields and dates. The static source review does not certify that either file
is the native downloaded artifact or that the server historically executed
exactly these bytes. The provider release and permission to export or share
remain separate UNKNOWN gates. No server DAT was supplied, so its bytes,
format, count, order and set comparison are `null`, not zero or matched.

## Execution record

The verified starting branch was `codex/research-plan-round10-20261106`, HEAD
`c91aba0f529303c0e5e710f1289054c9a3e27e90`, clean. Fixed review code
commit: `e1f5c0bd06802c3cfcf549fa6807aa4918bce14f`. Python 3.11 used this
repository's `src` on `PYTHONPATH`.

| Actual command (paths sanitized) | Outcome |
| --- | --- |
| `python -m pytest -q tests/test_review_m5_distribution_sql_sources.py` | Ten focused synthetic checks passed; exit 0. |
| `python scripts/review_m5_distribution_sql_sources.py run --handoff-zip <bound ZIP> --sql-2019 <asu1cjbn71x8ajp0.sql> --sql-merged <zgt8d3w8jqfnulor.sql> --output-dir <new ignored directory>` | Source review saved; exit 0. Source paths are retained in the private registry; the argument structure is exact and paths are sanitized here. No DAT flags were supplied. |
| `python scripts/review_m5_distribution_sql_sources.py verify-output --output-dir <same ignored directory>` | Saved-output verification passed; exit 0. |

The local run ledger spans 2026-11-12T09:49:37.783689+00:00 to
09:49:38.167382+00:00 (0.38 seconds, including its internal verification).
The separate verification command took 1.26 seconds at the tool boundary.
These are local wall times, not WRDS server timing. Peak memory is UNKNOWN.
There were zero new WRDS queries, Encoder sessions, return labels, financial
training runs, prediction evaluations and backtests.
