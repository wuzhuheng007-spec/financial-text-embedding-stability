# M5 WRDS three-file local intake and historical identity candidates

**Status, 2026-11-11:** `QUERY_SPEC_VERIFIED / LOCAL_FILE_MATCHES_RECEIPT_AND_QUERY_SHAPE / PROVIDER_UNIVERSE_COMPLETENESS_NOT_VERIFIED / HISTORICAL_CIK_LINK_VALIDITY_NOT_ESTABLISHED / FORMAL_M5_COHORT_NOT_SELECTED`. This is an `ENGINEERING_ONLY` local data-engineering audit. The unchanged [round-8 M5 candidate](../configs/m5_financial_protocol_round8.json) remains `PROPOSED`, `researcher_accepted=false`, and `NOT_RUN`; S2/S3 engineering acceptance does not authorize M5. No Encoder session, new return label, financial fit, prediction evaluation, or backtest ran.

The starting checkout was clean at `9266fd20d06443ccf9433dd7f65021c8e4efc0ff` on `codex/research-plan-round10-20261106`. Python 3.11 imported `finembed` from this checkout after setting `PYTHONPATH` to `src`; no model object was created. No existing raw CSV, SQL, v2 receipt, old starting export, calendar, or S2/S3 output was changed.

## Plan, actual execution, evidence, limitation, status

| Plan | Actual execution | Evidence | Limitation | Status |
| --- | --- | --- | --- | --- |
| Locate only registered inputs | Found the new starting export, CIK-CUSIP export, later security history, query SQL, and existing v2 private receipt in the registered local folders; retained the old smaller starting export separately | Exact paths and byte hashes in the ignored local registry | No broad personal-directory or WRDS account search | DONE |
| Bind query definition | Rehashed the supplied SQL and checked its single `crsp_q_stock.stksecurityinfohist` source, 36 selected fields, and only `secinfostartdt BETWEEN '1925-12-31'::date AND '2020-01-01'::date AND secinfoenddt >= '2020-01-01'` | SQL SHA-256 `f2a61ded20689753f221f1a53ae3d2357f5e8a398b2bf471ae9a7bcea945aec9`; user-supplied WRDS Query `11894962` page and parameter screenshots | The displayed 7,639 rows and 1.8 MiB are server-page claims; screenshot time zone and release vintage are unknown | `QUERY_SPEC_VERIFIED` |
| Independently check local starting-file shape | The local-only script read the CSV without printing rows, enforced all 36 SQL columns in order, as-of-date coverage, positive unique `PERMNO`, row width, and repeated source hash | 7,639 records, 36 columns, 7,639 unique security IDs; 1,920,219 local bytes; starting-file SHA-256 `f70e8aad2989a6f6a524f38bc1f873f6edb41ac05d5e92cd6743d9e54925b7d3` | Matching the page's row count and rounded size does not prove provider universe completeness or firm count | `LOCAL_FILE_MATCHES_RECEIPT_AND_QUERY_SHAPE` |
| Bind all three files to saved v2 work | Recomputed three source hashes, matched exact input paths and counts, and checked the v2 snapshot/link/history checker and validation-dictionary hashes against the saved receipt; preserved that receipt without rerunning the v2 audit | Link SHA-256 `8507672060dfd5728f29732e829df6c9c4b9af7c690c25612494d232008244c0`; later-history SHA-256 `89b7439642702d8a3b9a4859f944009b058865c62f9ea6ffdab1f7a8d805d9fc`; v2 receipt SHA-256 `651d4f508a956a3c64faf0b51ccdbcffbad3b3c226e984990378cb3bc9e00b42` | A hash match binds local bytes and saved software records, not an independently signed WRDS export or use license | PASS, LOCAL ONLY |
| Inventory historical identity | Matched every retained starting-security interval to all exact eight-character historical-CUSIP link rows, kept duplicate/multiple/no-match records and later-only securities separately, and wrote a private candidate/exception index | Ignored private index SHA-256 `de8df60b998d94d76a8452685bb94b27aa68ff864b5850a796b612824d3895bf`; aggregate-only summary SHA-256 `9d33b02743f3d7683462a286e0f25221c957ad00e75788152817091712ef6ebe` | Filing observation dates and `validated` quality do not establish effective CIK-to-security dates; every generated candidate has `accepted=false` | REVIEW CANDIDATES ONLY |
| Validate software and saved output | Ran only the focused synthetic intake tests and a local private-index readback that checked complete counts, source binding, and all `accepted=false` flags | 12 synthetic tests passed; readback `PRIVATE_INDEX_READBACK_PASS`; intake script SHA-256 `97ded6befe05d0759d1c289618a31d1cf4eab6f965d34fb01768fef1b23cae1c` | Synthetic tests do not verify real financial identity or scientific eligibility | PASS, SOFTWARE ONLY |

The local registry was created at `2026-11-11T02:22:41.595233+00:00`; this is **record creation time**, not WRDS query submission, acquisition, publication, or the screenshots' unknown local time zone. Product-release vintage and project export/license terms remain unknown in the registry. The existing v2 receipt has no independent provider signature. The new binder tests local consistency and query shape; it does not certify origin beyond the available query materials.

## Aggregate local inventory, not an admitted sample

Counts below come from the aggregate-only ignored summary, except the exact-duplicate link-row count, which a local offline readback counted from the bound private exception index. Both outputs bind the three file hashes, v2 receipt hash, and intake-script hash above. They are counts of rows, securities, intervals, or candidate relationships as labeled; none is an independent firm or financial-observation count.

| Unit | Local result | Interpretation |
| --- | ---: | --- |
| Starting as-of security rows / unique `PERMNO` | 7,639 / 7,639 | Structural as-of security identities; no CIK cohort chosen |
| Link-table rows | 89,987 | Raw CIK-CUSIP observations, including 5,845 exact duplicate link rows flagged for review |
| Later-history rows | 42,197 | 21,765 attach to starting securities; 20,432 belong only to 6,922 other securities and remain outside the starting set |
| Intervals retained for starting securities | 29,404 | Starting row plus available later intervals; saved v2 interval-issue ledger reports no overlap/gap issue codes for these retained rows |
| Starting securities with more than one historical CUSIP | 1,140 | Observed identifier changes; no as-of CIK is carried forward automatically |
| Starting securities whose retained history ends before 2025-12-31 | 2,139 | An early history endpoint, **not** a verified delisting or label outcome |
| Starting intervals with unusable historical CUSIP / no exact link row / multiple CIK candidates | 2,142 / 7,124 / 5,973 | Review categories overlap; no category is an accepted join |
| Interval-to-link candidate rows / private exception records | 47,125 / 33,293 | Many-to-many candidate and review entries, not unique securities or independent samples |
| Accepted CIK links / formal M5 cohort | 0 / none | No automatic validity, primary-security, or cohort decision |

The supplied dictionary screenshot summary classifies `validated=3` as nine-digit CUSIP and name match, `2` as eight-digit and name match, `1` as eight-digit match with name mismatch, and `0` as no eight-digit CUSIP Master match. The local v2 dictionary records screenshot SHA-256 `cd7cf861255b1c236b9704a17a700f14e3942180e9da56caf1df1123c43c8f91` and dictionary-file SHA-256 `74740f29e2c2c31baecbb0d746583fd8a7eeac9772a263ca9496d545449122cb`; its screenshot was not re-opened in this run. Validation-master vintage remains `UNKNOWN`. Even a code `2/3` or a unique CIK is not a time-valid primary-security determination. The [WRDS linking description](https://wrds-www.wharton.upenn.edu/pages/wrds-research/database-linking-matrix/linking-sec-with-crsp/) says the bridge comes from Schedule 13D/G CIK/CUSIP observations; it does not establish that first/last observed filing dates are link-validity endpoints.

The private index retains each starting `PERMNO`'s intervals, exact historical CUSIP, all matched link rows, quality flags and interval issues; it also retains later-only intervals and exceptions without admitting them. It is stored only under ignored `artifacts/`; no identifiers, raw rows, licensed values, or private absolute paths are included in this report or Git. Its source bindings were read back from disk; all candidate and interval `accepted` flags remained false. The original v2 receipt, old smaller starting export and old results remain intact.

## Next acquisition steps and gates

These are **next needed records**, not permission to expand the M5 cohort. CIZ names and item spellings below are documented in the [official CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf); the actual authenticated WRDS query-table names, field exposure, release, and project permission must be checked before each request. Do not download the already locally bound three-file roles again merely to advance status.

| Priority / status | Product or source and fields | Scope / use | Blocking evidence |
| --- | --- | --- | --- |
| 0 / existing but source metadata incomplete | Current `crsp_q_stock.stksecurityinfohist` starting export, later-history export, and WRDS CIK-CUSIP bridge | Preserve existing hashes and query definitions; obtain release/version and export/use terms for the current files, plus the later-history/bridge query conditions | Provider universe coverage and bridge date semantics remain unverified; do not silently repeat a download |
| 1 / not obtained | CRSP CIZ `StkIssuerInfoHist`: `PERMCO`, `IssInfoStartDt`, `IssInfoEndDt`, `IssuerBegDt`, `IssuerEndDt`, `IssuerNm`, `IssuerType`, `IssuerStatusType` | Complete dated issuer history for every starting `PERMCO`, retaining inactive and changed names without current-status filtering | Confirm actual WRDS table/fields, release and permission; no company-level identity history is bound yet |
| 2 / not obtained | CRSP CIZ `StkDelists`: `PERMNO`, `DelistingDt`, `DelActionType`, `DelStatusType`, `DelReasonType`, `DelPaymentType`, `DelPERMNO`, `DelPERMCO`, `DelRet`, `DelRetMissType`, `DelNextDt`, `DelNextPrc`, `DelDlyDt` | Every starting `PERMNO` with events from 2020-01-01 through the existing 2026-02-28 buffer; retain successors and missing terminal values | Confirm table exposure and release; researcher has not fixed the exit-label policy |
| 3 / not obtained for this universe | CRSP CIZ `StkDlySecurityData`: `PERMNO`, `DlyCalDt`, `DlyClose`, `DlyPrc`, `DlyPrcFlg`, `DlyRet`, `DlyRetx`, `DlyRetMissFlg`, `DlyFacPrc`, `DlyDelFlg` | Initial `PERMNO` set, 2019-11-01 through 2026-02-28; existing 2025 five-firm bars are development-only | Confirm table exposure, adjustment and historical close-availability evidence; no returns are calculated here |
| 4 / not obtained | CRSP CIZ `StkDistributions`: `PERMNO`, `DisExDt`, `DisSeqNbr`, `DisType`, `DisDivAmt`, `DisFacPr`, `DisFacShr` | Initial `PERMNO` set and same buffered date span, preserving splits and dividends | Confirm release/permission and reconcile price adjustment; target remains the proposed price-return definition |
| 5 / partly present | Existing source-bound XNYS machine calendar, manifest SHA-256 `ebd95c7f293088545706a0fac65c8c25427748a0e1f01ded88b3ee950c0d3565` | Reuse only for securities whose market applicability is verified; obtain independent official calendars for other admitted markets | Eligible exchange/MIC set and market-specific applicability are unfrozen |
| 6 / development texts only | SEC original 10-K, amendments, exact Item 1 text bytes and accession; first-public/no-later-than/proxy evidence | Future 2020–2025 eligible filing events after cohort and time policy are fixed; the five saved development texts do not supply this panel | Historical text vintage, public-time clock and strict-as-of versus retrospective interpretation are open |

Historical CRSP `SecInfoEndDt` is used inclusively in the source query. The project's [canonical import contract](../docs/M5_HISTORICAL_IMPORT_ROUND9.md) uses half-open `[valid_from, valid_to)` intervals; a later importer must explicitly convert or keep vendor dates separate. No conversion or time-valid CIK join is claimed by this intake.

## Decisions before formal M5 use

1. **Research interpretation:** strict historical-as-of prediction or a later fixed-snapshot retrospective study. The latter cannot establish absence of look-ahead contamination.
2. **Cohort and security:** eligible 2020 markets/types, unique documented primary share among classes, and ambiguous/unmatched CIK treatment. The planned 200-CIK hash rule cannot run until a complete eligible CIK universe exists.
3. **Time and identifiers:** evidence for CIK-CUSIP observation-date meaning, dated security link, text-publication and finalized-close availability; never infer a validity interval from the bridge's first/last filings alone.
4. **Labels and exits:** price adjustment, distribution and terminal-value policy, plus independent calendar applicability. Missing bars or exits cannot be removed to make the panel complete.

## Actual commands and software verification

All paths to licensed CSVs and the private v2 receipt were passed as explicit local arguments and are recorded in the ignored registry. This shareable report substitutes role placeholders for those private absolute paths. The v2 checker was **not rerun**.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& <verified-Python-3.11> scripts/audit_m5_wrds_intake.py `
  --snapshot <local-starting-export> --links <local-CIK-CUSIP-export> `
  --changes <local-later-history-export> --sql <local-query-SQL> `
  --receipt <existing-v2-private-receipt> --tool-dir <existing-v2-tool-dir> `
  --output-dir artifacts/m5_wrds_intake_20261111_v1
# exit 0: M5_WRDS_LOCAL_INTAKE_AND_HISTORY_CANDIDATES_COMPLETE
#          M5_PROVIDER_UNIVERSE_AND_HISTORICAL_LINK_VALIDITY_PENDING

& <verified-Python-3.11> -m pytest -q tests/test_audit_m5_wrds_intake.py
# exit 0: 12 passed, SYNTHETIC_TEST_ONLY

# Local offline readback of ignored candidate index and aggregate summary:
# exit 0: PRIVATE_INDEX_READBACK_PASS; complete counts and accepted=false verified
```

No unfiltered test suite, Encoder, new embedding, return calculation, predictor, score, backtest, paid API, WRDS query, or bulk acquisition was run. The intake code and tests are [here](../scripts/audit_m5_wrds_intake.py) and [here](../tests/test_audit_m5_wrds_intake.py); the original v2 checker is retained outside Git. This report contains historical-read aggregate counts only, not financial results.
