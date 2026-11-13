# M5 WRDS next download: the 2020 security universe and CIK bridge

**Current acquisition note, 2026-11-12:** The [admission readiness reconciliation](M5_DATA_ADMISSION_READINESS_20261112.md) is the current status source. The security/issuer and filtered Delist files, eight Daily windows, and two Distribution windows have local engineering receipts. The two Distribution SQL statements have bounded static content review, while native page/DAT attachment and Daily backend JOIN remain open. The download sequence below is preserved as the historical acquisition plan; it is not an instruction to redownload these files. See the [minimum new material](M5_MINIMUM_MATERIALS_20261112.md).

**Current follow-up, 2026-11-12:** Eight bounded daily-file receipts have since
been received and compared. The daily-file step below is retained as its
historical acquisition plan; the next manual source role is the
[distribution and corporate action plan](M5_WRDS_DISTRIBUTIONS_NEXT_DOWNLOAD.md).
Saved receipt identity does not establish provider completeness or formal M5
admission.

**Local intake update, 2026-11-11:** The [three-file security/link intake](../reports/2026-11-11_m5_wrds_three_file_local_intake.md)
and [two issuer-history exports](../reports/2026-11-11_m5_issuer_history_local_intake.md)
retain their separate source bindings. A [filtered delisting export](../reports/2026-11-11_m5_delist_local_intake.md)
has also passed local structural and source checks. Its SQL selects
`DelistingDt` from 2019-11-01 through 2026-02-28 and only
`GDR/GEX/GLI/LOS/MER`, with a PERMNO left join to undated
`StkSecurityInfoHdr`. This is neither an unfiltered single-table delisting
universe nor historical Header identity evidence. The broad raw query does not
extend the candidate cohort. Query ID, page count, product release, export
terms, provider completeness, historical CIK validity and formal admission
remain open. The next unobtained role is `StkDlySecurityData`, then
`StkDistributions`. Do not redownload the same raw roles to manufacture a new
status.

**Status:** acquisition instructions only; M5 remains `PROPOSED`,
`researcher_accepted=false`, `NOT_RUN`. The unchanged
[round-8 candidate](../configs/m5_financial_protocol_round8.json), SHA-256
`f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38`,
defines the cohort and later event comparison. This page does not authorize a
financial run or select securities using the five development firms.
Official pages below were checked on **2026-11-11**. A public product page
establishes that a product is documented; it does not establish this account's
table permission, release vintage, export terms, or the actual contents of a
future file.

## Preserved acquisition path: historical security information at 2020-01-01

1. In your own WRDS session, open the [CRSP Stock (Quarterly) product catalog](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/center-for-research-in-security-prices-crsp/)
   (`crsp_q_stock`) and its [authenticated data dictionary](https://wrds-www.wharton.upenn.edu/data-dictionary/crsp_q_stock/).
   Find **`StkSecurityInfoHist`** (the CIZ source) or the documented WRDS
   **`StockNames_V2`** mapping. The [WRDS CIZ webinar](https://wrds-www.wharton.upenn.edu/documents/2084/Webinar.pdf)
   maps these names; a [WRDS macro](https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/crsp-ciztosiz-macro/)
   references `crsp.stkSecurityInfoHist`. Record the *actual* table identifier
   displayed by your authenticated query page. Public pages do not verify
   `crsp_q_stock.stksecurityinfohist` as a queryable SQL identifier.
2. Request `PERMNO`, `PERMCO`, `SecInfoStartDt`, `SecInfoEndDt`, `CUSIP`,
   `CUSIP9`, `Ticker`, `TradingSymbol`, `SecurityNm`, `IssuerNm`, `ShareClass`,
   `PrimaryExch`, `ConditionalType`, `TradingStatusFlg`, `SecurityActiveFlg`,
   `SecurityType`, `SecuritySubType`, `ShareType`, `USIncFlg`, and `IssuerType`.
   These CIZ item names are supported by the [CRSP CIZ database guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf)
   and the [WRDS conversion macro](https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/crsp-ciztosiz-macro/).
   Confirm each exact field in the logged-in dictionary; record any missing
   item instead of inventing an alias.
3. Use the **inclusive history intersection**
   `SecInfoStartDt <= 2020-01-01 <= SecInfoEndDt`. The WRDS macro uses the
   same inclusive date-range comparison. This reconstructs a later-vintage
   retrospective snapshot, not what was known on 2020-01-01. If the web form
   cannot express both conditions, stop and record that limitation; a 2020
   price-row list or current `StkSecurityInfoHdr` is not a substitute.
4. Do **not** filter by current ticker, available price, current active status,
   security type, issuer type, exchange, or later survival in this raw export.
   Preserve inactive and eventually delisted securities and all ambiguous
   classes. Save as
   `m5_crsp_stockq_stksecurityinfohist_asof_20200101_raw.csv` outside Git.
   Save the query parameters, product/release label, retrieval time, row count,
   file SHA-256, and applicable export/use terms in a private local receipt.

This first file supplies historical issue (`PERMNO`) and issuer (`PERMCO`)
identifiers, as-of-date type, venue, ticker/CUSIP and name evidence. It does
**not** contain a verified CIK-to-issue link or a documented unique primary
share choice. The [CRSP CIZ database guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf)
distinguishes historical `StkSecurityInfoHist` from header fields holding
recent identifiers. The 2020 cohort cannot be selected at the CIK level from
this file alone.

## Remaining files and received issuer history, in acquisition order

| Order and suggested private filename | Product / verified entry and raw table | Fields to request; query scope | Admission purpose; unverified gate |
| --- | --- | --- | --- |
| 2. `m5_crsp_stockq_stksecurityinfohist_2020cohort_history_raw.csv` | Same [CRSP Stock (Quarterly) dictionary](https://wrds-www.wharton.upenn.edu/data-dictionary/crsp_q_stock/); CIZ `StkSecurityInfoHist` / WRDS `StockNames_V2` mapping. | All dated rows for every `PERMNO` present in file 1, without a current-status or later-price filter. Keep the file-1 fields, including full `CUSIP`/`CUSIP9`, ticker/name, exchange/type and `SecInfoStartDt`/`SecInfoEndDt` history. | Captures changes and later exits of the *initial* securities and builds the full historical CUSIP set. Confirm the authenticated table's field names and list-filter facility; do not trim history to only rows starting in 2020. |
| 3. `m5_wrds_sec_wciklink_cusip_bridge_raw.csv` | [WRDS SEC Analytics Suite CIK-CUSIP query](https://wrds-www.wharton.upenn.edu/pages/get-data/wrds-sec-analytics-suite/wrds-sec-linking-tables/cik-cusip-link-table/); official WRDS material calls the dataset `WCIKLINK_CUSIP`. | Request the full CIK-CUSIP bridge, or every CUSIP seen in file 2 if a complete, auditable list filter is supported. Do not prefilter to current tickers, the five development firms, or selected 200 CIKs. The existing *pilot export* has `cik,cusip,cusip_full,cikdate1,cikdate2,validated`; these spellings and date/validation meanings still need the authenticated field dictionary. | Generates candidate CIK↔CUSIP associations to join to dated CRSP `CUSIP`/`PERMNO`; retain unmatched and multiple matches. [WRDS says](https://wrds-www.wharton.upenn.edu/pages/wrds-research/database-linking-matrix/linking-sec-with-crsp/) this bridge is built from Schedule 13D/G filings. Its first/last observed filing dates must **not** be treated as the link's valid-from/to without field-definition evidence. [Product page](https://wrds-www.wharton.upenn.edu/pages/grid-items/wrds-sec-linking-tables/) says a WRDS SEC Analytics Suite subscription is required; current project-wide entitlement is unverified. |
| 4. Received as two local issuer exports | The preserved SQLs query `crsp_q_stock.stkissuerinfohist`; CIZ `StkIssuerInfoHist` is defined by the [CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf). | Both SQLs select the same 18 fields, including `PERMCO`, `IssInfoStartDt`, `IssInfoEndDt`, `IssuerBegDt`, `IssuerEndDt`, `CNUM`, issuer status/type and counts. The start-date windows are above; no PERMCO or survival filter was used. | Local structural binding and unaccepted issuer interval candidates only. `IssInfoStartDt`/`IssInfoEndDt` describe the history-row range; `IssuerBegDt`/`IssuerEndDt` are not substituted as its effective dates. `CNUM` does not establish CIK, and issuer status/end does not establish a delisting. Release, export terms and provider completeness remain unverified. |
| 5. Received: bounded filtered `StkDelists` export | Preserved SQL queries `crsp_q_stock.stkdelists`, left-joining `crsp_q_stock.stksecurityinfohdr` on PERMNO with no event-date condition. The [CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf) distinguishes those roles. | Event table: 19 selected fields including `PERMNO`, `DelistingDt`, `DelActionType`, `DelRet`, `DelRetMissType`, `DelDlyDt` and successor IDs. Header: `PrimaryExch`, `NASDIssuno`, `SICCD`. SQL filters `DelistingDt` 2019-11-01 through 2026-02-28 and the five actions above; no PERMNO list or LIMIT. | Local receipt and unaccepted event candidates only. Header values are not event-time attributes. Five-action filtering, possible join multiplicity, unknown release/use terms and unknown provider completeness prevent a claim of complete delist coverage. |
| 6. Next: `m5_crsp_stockq_stkdlysecuritydata_2019_2026_raw.csv` | Same CRSP product/dictionary; CIZ [`StkDlySecurityData`](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf). | Use the locally generated, private list of all 7,639 starting `PERMNO` identifiers, excluding unaccepted successors. Request `PERMNO`, `DlyCalDt`, `DlyClose`, `DlyPrcFlg`, `DlyFacPrc`, `DlyRet`, `DlyRetx`, `DlyRetMissFlg`, `DlyDelFlg`, `DlyDistRetFlg` if exposed; query `2019-11-01` through `2026-02-28` as an overfetch buffer from the [existing calendar rules](../configs/nyse_calendar_rules_round9.json). | Assess a potential 12,611,989 security-by-weekday grid cells before request; this is not an observed WRDS row count or universal trading calendar. Confirm authenticated fields, list-filter mechanism, release, permission/export terms and batch/size limits. Daily bars, adjustments, actions and close availability remain unverified. |
| 7. `m5_crsp_stockq_stkdistributions_2019_2026_raw.csv` | Same CRSP product/dictionary; CIZ [`StkDistributions`](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf). | Retained `PERMNO`, same buffer dates; request `DisExDt`, `DisSeqNbr`, `DisType`, `DisDivAmt`, `DisFacPr`, `DisFacShr` alongside `PERMNO`, subject to authenticated dictionary confirmation. | Provides split/dividend/action evidence. The proposed primary target excludes dividends; this file does not authorize a total-return or trading-value conclusion. |

### Manual daily-file staging after local upload-list verification

Run `python scripts/prepare_m5_daily_permno_upload.py prepare` with this
checkout's Python 3.11 and `PYTHONPATH=src`, then run its `verify-output` mode.
The converter checks the saved delisting manifest, prior intake receipt,
original 2020 starting snapshot and exact identifier set without rerunning the
delisting or issuer audit. It writes the ASCII, one-PERMNO-per-line TXT and a
private provenance record under ignored `artifacts/`. `M5_DAILY_PERMNO_UPLOAD_READY`
means only that the local upload list is source-bound. It is not an M5 cohort,
WRDS query result or financial experiment. Do not upload the private list to Git.

In your WRDS session, navigate to **CRSP → Quarterly Update → Stock - Version 2
→ Daily Stock File**. Select the **PERMNO** code type and upload the verified
TXT. Do not select **Search the entire database**. The public [WRDS CRSP
demo](https://wrds-www.wharton.upenn.edu/demo/crsp/form/) documents the generic
one-code-per-line TXT mechanism, but it is a monthly-file example. The current
authenticated daily form, its query table, columns, list filter, product access,
release and export terms must be checked directly and saved with the first SQL
and result-page receipt. The CIZ `StkDlySecurityData` name is a vendor source
role, not a verified WRDS PostgreSQL table name for this account.

Use the **same verified TXT** for these inclusive, nonoverlapping calendar
windows. They are download batches, not model train/validation/test splits.
Submit only the first, short segment initially; inspect and retain the CSV or
ZIP, SQL, Query ID, result-page count/size and input parameters before any
remaining segment is requested.

| Batch | Start | End | Purpose |
| ---: | --- | --- | --- |
| 1 | 2019-11-01 | 2019-12-31 | First query and structure check |
| 2 | 2020-01-01 | 2020-12-31 | Later manual batch |
| 3 | 2021-01-01 | 2021-12-31 | Later manual batch |
| 4 | 2022-01-01 | 2022-12-31 | Later manual batch |
| 5 | 2023-01-01 | 2023-12-31 | Later manual batch |
| 6 | 2024-01-01 | 2024-12-31 | Later manual batch |
| 7 | 2025-01-01 | 2025-12-31 | Later manual batch |
| 8 | 2026-01-01 | 2026-02-28 | Later manual batch |

For the first authenticated form, compare the requested fields individually
with its current dictionary: `PERMNO`, `DlyCalDt`, `DlyClose`, `DlyPrc`,
`DlyPrcFlg`, `DlyPrevDt`, `DlyPrevPrc`, `DlyPrevPrcFlg`, `DlyFacPrc`,
`DlyRet`, `DlyRetx`, `DlyRetMissFlg`, `DlyRetDurFlg`, `DlyDelFlg`,
`DlyDistRetFlg`, `DlyVol`, `DlyOrdDivAmt`, and `DlyNonOrdDivAmt`.
These are vendor-guide field names, not confirmation of the current WRDS
web-query names. Record unavailable fields; do not substitute similarly named
legacy fields. Prefer CSV, ZIP compression and `YYYY-MM-DD` dates if offered.
Do not request an unbounded **All** field set on the large daily table.

Keep `DlyClose` and `DlyPrc` separate: the vendor guide permits a quote-derived
`DlyPrc` when no transaction close is available. Retain the price flags.
`DlyFacPrc` is a price adjustment factor used in return construction; do not
relabel it a split-only cumulative factor. Receive `DlyRet` and `DlyRetx` as
raw fields only. No return labels, missing-value fill or combination with
`DelRet` is authorized at this acquisition step. Do not add current-ticker,
price-completeness, positive-price, return-availability, survivor, market or
security-type filters to the initial raw download, and do not automatically add
unaccepted successor identifiers.

The raw CIZ name `StkSecurityInfoHist`, WRDS mapped name `StockNames_V2`,
product code `crsp_q_stock`, and macro libref `crsp.stkSecurityInfoHist` are
**different identifiers**. The public WRDS catalog and training materials
document them, but the logged-in dictionary must identify the actual query
table and fields for this account. Do not write a guessed
`crsp_q_stock.stksecurityinfohist` SQL query. The exact source-release vintage
for each download must be recorded; an as-of-date row extracted in 2026 does
not by itself establish historical data availability in 2020.

## Local staging after files 1–3, before any cohort selection

Retain a raw-file ledger with all rows and hashes. For review, compare the
candidate's U.S. exchange-listed operating common-share rule with CIZ
`ShareType=NS`, `SecurityType=EQTY`, `SecuritySubType=COM`, `USIncFlg=Y`,
`IssuerType in {ACOR,CORP}` and dated `PrimaryExch`. The
[WRDS conversion macro](https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/crsp-ciztosiz-macro/)
maps those type combinations to legacy common-share codes 10/11; the
[CRSP exchange flag table](https://www.crsp.org/wp-content/uploads/appendix/FlagType_EC.html)
defines exchange values. Keep all other raw rows and an exclusion ledger. The
eligible exchange set and unique *primary* issue rule still require a research
decision; no unverified primary flag is assumed. Join issue history by
`PERMNO`, issuer history by `PERMCO`, and CIK associations by historical CUSIP
plus a documented date rule. Do not select by future price completeness.

Only after the complete 2020 CIK universe and link ambiguities are audited
could the unchanged candidate exclude its five development CIKs and apply its
specified SHA-256 CIK-order rule to plan up to 200 distinct CIKs. No 200-CIK
file or formal M5 observation is claimed here. See the
[historical import contract](M5_HISTORICAL_IMPORT_ROUND9.md) for the later
canonical mapping and structural-only validator. A `PASS_STRUCTURE_ONLY`
receipt cannot establish source entitlement, a primary-share rule, text
availability, or formal financial admission.

## Permission and decision gates

| Gate | Current evidence | Needed before the dependent step |
| --- | --- | --- |
| Received CRSP history exports | The specific security and issuer exports have local structural and hash-bound receipts. | Confirm product release, project-wide permitted use/export terms and provider-universe completeness separately. Local receipts do not certify a formal cohort. |
| Received bounded CRSP delisting export | The specific filtered SQL/CSV pair passed local source binding; the [delisting intake report](../reports/2026-11-11_m5_delist_local_intake.md) preserves its exact date/action and undated Header join scope. | Confirm broader action coverage, source event uniqueness, product release and applicable export/use terms separately; this receipt does not authorize formal M5 exit labels. |
| Next CRSP daily and distribution roles | The [CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf) documents both tables, but no project full-panel daily or distribution file has been received. | Confirm authenticated fields, targeted list upload, batch size, release, permission and export terms before the next request. |
| WRDS SEC bridge access and meaning | The five-firm checker source and user-reported exports support a bounded pilot. The [WRDS product page](https://wrds-www.wharton.upenn.edu/pages/grid-items/wrds-sec-linking-tables/) documents a subscription requirement. | Confirm project permission, exact authenticated fields and their date semantics; preserve unmatched CIKs. This blocks formal CIK↔issue admission. |
| Research interpretation | Round-8 recommends a later fixed-byte retrospective study; strict historical-as-of evidence remains blocked. Type/exchange membership, unique primary share, text/public-time and exit label policies are not frozen. | Researcher must freeze these choices before any formal M5 execution. Downloading raw fields is ordinary preparation, not acceptance. |

**Prior engineering receipt, transferred for this planning round:** the
preceding conversation records an **agent-launched**, local offline five-firm
join with terminal token `WRDS_FIVE_FIRMS_TEXT_LABEL_JOIN_PASS`, process exit 0,
five saved index rows, and index SHA-256
`90c14e3196678c4ce78d025cf9c50746c4a68e40a30736b2a8d1d30bec2a6642`.
That turn also reported local hash checks. This round does **not** reopen the
private index or WRDS files; the receipt is retained as transferred prior-run
evidence, not a new M5 import or independent financial result. The agent
started the real check rather than the previously planned user double-click;
that execution-mode deviation is preserved. The transcript does not establish
whether raw data entered a model context, and no inference is made.

**This round:** `ENCODER_NOT_RUN`, `FINANCIAL_TRAINING_NOT_RUN`,
`PREDICTION_EVALUATION_NOT_RUN`, `BACKTEST_NOT_RUN`. No WRDS account was
probed, no new licensed data was downloaded, and M5 remains unfrozen.
