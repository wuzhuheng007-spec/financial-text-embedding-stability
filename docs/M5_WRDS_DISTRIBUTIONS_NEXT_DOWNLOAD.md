# M5 distribution and corporate action acquisition plan

**Readiness pointer, 2026-11-12:** See the [current admission matrix](M5_DATA_ADMISSION_READINESS_20261112.md) and [minimum material list](M5_MINIMUM_MATERIALS_20261112.md). The dated first-extract note below accurately records what was unavailable at that earlier checkpoint; two SQL statements have since been inspected as local content. Their native Query-page origin and server Input DAT remain unverified. The ten-code `DisType` selection differs from the unfiltered acquisition proposal and cannot certify a complete action universe.

**Current engineering status, 2026-11-12:** The bounded
[2019 first-segment intake](../reports/2026-11-12_m5_distribution_first_intake.md)
and [2020-2026 merged intake](../reports/2026-11-12_m5_distribution_merged_intake.md)
have completed local structural receipt and saved-output verification. A
[targeted review](../reports/2026-11-12_m5_distribution_2020_single_group_review.md)
found zero rows for the sole unmatched security-date key in the bound saved
2020 daily extract; its absence has no established cause. Two user-supplied
`.sql`-named files have undergone [offline static source review](../reports/2026-11-12_m5_distribution_sql_source_review.md):
their fields, date predicates, literal type filter and inline identifier-list
order agree with the bounded saved inputs. Association with the prior WRDS
Query pages is provisional without a native download-origin note or execution
log. Server Input DAT evidence and the earlier daily backend JOIN remain
unverified.
These engineering receipts do not freeze M5, which remains PROPOSED,
researcher_accepted=false, and NOT_RUN.

**First bounded extract update, 2026-11-12:** The
[2019-11-01 to 2019-12-31 local intake](../reports/2026-11-12_m5_distribution_first_intake.md)
passed structural receipt and saved-output verification. The user-pasted
parameters for Query 11900944 include an explicit ten-code `DisType` filter,
which differs from the unfiltered request proposed below. The native SQL and
server Input Files DAT have not been obtained. The next 2020 batch remains a
manual proposal and must preserve this scope distinction.

**Status:** manual acquisition plan, ENGINEERING_ONLY. The eight daily-file
segment receipts span the retained acquisition windows; they do not establish
provider completeness or formal M5 admission. The round-8 M5 candidate remains
PROPOSED, researcher_accepted=false, NOT_RUN. No distribution query has been
submitted by this plan.

## First action: verify the authenticated form

In the user's WRDS session, start at the [CRSP Stock (Quarterly) product
catalog](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/center-for-research-in-security-prices-crsp/)
and locate the current Stock Version 2 distribution or corporate action query
form. Save the form title, product label, variable dictionary, date-filter
options, identifier-upload controls, automatic Header fields or joins, release
label, and export/use notice. The public [CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf)
documents `StkDistributions` as a source role. It does not establish this
account's current WRDS query-table identifier, exact displayed columns, form
capabilities, release, entitlement, or export terms. If the form cannot be
identified or mapped, stop before submitting a query and retain the gap.

## Conditional manual query settings

| Setting | Proposed entry | Verification gate |
| --- | --- | --- |
| Source | Current authenticated CRSP Stock (Quarterly), Stock Version 2 distribution form corresponding to CIZ `StkDistributions` | Save the actual form and dictionary identity; do not invent a SQL table name. |
| Identifiers | Upload the existing source-bound TXT of 7,639 starting `PERMNO` values; retain all listed issues, including later inactive ones | Confirm PERMNO upload and input attachment on this form. Do not add successors, current tickers, or a selected M5 cohort. |
| Date axis | Proposed `DisExDt`, inclusive 2019-11-01 through 2026-02-28 | Confirm the form filters on `DisExDt`. This is a data-acquisition buffer, not a claim that every declaration, record, or payment date lies within it. |
| Events | All available types and sequences, with no event, survival, market, dividend-presence, or price-availability screen | Confirm page defaults and save all selected parameters. Multiple events and components on one ex-date are retained. |
| Output | CSV, ZIP compression, ISO dates if supported | Save original download, input attachment, query parameters, Query ID, result-page count/size, download SQL if actually available, retrieval time with stated timezone, and file SHA-256. |

The first manual request should be the short **2019-11-01 to 2019-12-31**
segment after the form mapping is confirmed. Inspect its header, date bounds,
identifier-list binding, duplicate event keys and any automatic joins before
requesting the remaining nonoverlapping calendar segments: each full year
2020–2025, then 2026-01-01 to 2026-02-28. Reuse the same bound identifier TXT.
These are download batches, not training or validation splits. Do not infer
that the daily-query form's upload or date controls also exist on this form.

## Candidate fields to map in the live dictionary

The [official CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf)
defines the following `StkDistributions` fields. Request them only if the
authenticated form exposes the corresponding variables, recording unavailable
fields and their impact rather than substituting legacy SIZ names:

| Role | CIZ guide field names to check |
| --- | --- |
| Issue and event identity | `PERMNO`, `DisExDt`, `DisSeqNbr`, `DisPERMNO`, `DisPERMCO` |
| Event and component classification | `DisType`, `DisOrdinaryFlg`, `DisFreqType`, `DisPaymentType`, `DisDetailType`, `DisTaxType`, `DisAmountSourceType` |
| Amount, currency, adjustment | `DisDivAmt`, `DisOrigCurType`, `DisFacPr`, `DisFacShr` |
| Other event dates | `DisDeclareDt`, `DisRecordDt`, `DisPayDt` |

`DisExDt` denotes the first trading date without the distribution right;
`DisSeqNbr` distinguishes events sharing an ex-date. Declaration, record and
payment dates have different meanings and can cross the requested ex-date
window. Preserve complete event rows and flag boundary cases during intake.
Record amount units and currency definitions from the live dictionary; do not
assume that `DisDivAmt` is USD per share. Treat `DisFacPr` and `DisFacShr` as
documented adjustment-factor fields without silently treating them as a
single-event split ratio. Legacy `DISTCD` and `DIVAMT` are not presumed to be
current CIZ query variables.

## Intake and interpretation gates

The local receipt must bind the uploaded identifier file, original ZIP/CSV,
current form and dictionary evidence, output header, parameters, any obtained
SQL, code identity, hashes, counts, duplicate event keys, and all exceptions.
Keep event multiplicity intact. Compare with daily-row duplicates only after
both source roles and join behavior are independently established; do not
deduplicate the daily export or compound returns as part of receipt work.

The current authenticated distribution field mapping, backend joins, product
release, project license/export scope, provider completeness, historical CIK
validity, event-to-label policy, and formal M5 cohort remain UNKNOWN or
unfrozen. A distribution file would be an engineering input, not a financial
outcome or acceptance of the M5 protocol.
