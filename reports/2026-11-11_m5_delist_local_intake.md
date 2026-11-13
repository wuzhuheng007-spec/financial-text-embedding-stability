# M5 filtered delisting export: local source binding and event review

**Scope:** `ENGINEERING_ONLY`. The local SQL/CSV pair was checked against the
previously source-bound 2020 starting securities, later security history and
issuer-history index. Every event and successor association remains an
unaccepted review candidate. The unchanged [round-8 M5 proposal](../configs/m5_financial_protocol_round8.json)
has SHA-256 `f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38`
and remains `PROPOSED / researcher_accepted=false / NOT_RUN`.

The starting checkout was clean at `136b08ebed3f64257ca842853d755f4e160af1e4`
on `codex/research-plan-round10-20261106`. The selected Python 3.11 interpreter
imported `finembed` from this checkout after `src` was put on `PYTHONPATH`.
No Encoder, new return label, finance fit, prediction score or backtest ran.

## Plan, actual work, evidence, change, limitation and status

| Plan | Actual work | Evidence | Change or limitation | Status |
| --- | --- | --- | --- | --- |
| Locate and bind the new export | Found one SQL/CSV pair in the registered local location; verified the original SQL byte hash and exact SELECT/FROM/JOIN/WHERE semantics | SQL SHA-256 below; [audit source](../scripts/audit_m5_delist_intake.py) | Query ID, provider page count, download time, product release and project use/export terms were not supplied | `QUERY_SPEC_VERIFIED` |
| Check local CSV structure | Rehashed before and after read; checked all 22 ordered fields, row width, positive primary PERMNO, date/action filter, and retained nullable-field review notes | Final ignored registry and summary hashes below | The actual query is **date- and five-action-filtered**, with a PERMNO `LEFT JOIN` to an undated Header subquery; it is not an unfiltered single-table event universe | `LOCAL_DELIST_SOURCE_BOUND_REVIEW_ONLY` |
| Preserve event versus Header provenance | Saved the 19 selected `StkDelists` fields separately from `PrimaryExch`, `NASDIssuno`, `SICCD` supplied by `StkSecurityInfoHdr`; kept all source record numbers, row hashes and raw values in the ignored index | Private index and manifest hashes below | Header fields have no event-date condition and cannot define historical market, industry or identity. Source event uniqueness and join multiplicity are not independently verified | DONE |
| Review duplicate patterns and starting membership | Kept all raw rows; reviewed `(PERMNO, DelistingDt, DelActionType)` as a grouping key without declaring it a primary key; compared with the previously bound starting PERMNO set | Machine counts below | No row was removed, no later-only event was added to the initial set, and no successor was automatically joined | DONE |
| Diagnose prior history | Compared event dates descriptively with retained security/issuer ranges and subdivided the prior 4,097 multi-issuer intersections | Separate derived view in the new private index | Closed-date comparison is an analytic diagnostic, not a verified provider endpoint rule. A history end is not a delisting event | `UNACCEPTED_CANDIDATES` |
| Read back saved files | Rehashed new source, code, prior artifacts and all saved files; re-read local CSV and recomputed groups, availability, source notes, identity diagnostics and issuer refinement | `verify-output` returned `M5_DELIST_SAVED_OUTPUT_VERIFIED`, exit 0 | File consistency cannot establish complete action coverage, financial admission or a valid return policy | PASS |

## Exact source scope and machine counts

The original SQL SHA-256 is
`dfd1533ecc737f47891d7105a9cd0542a8dc7db053d603ba894059e9452b318f`.
It queries `crsp_q_stock.stkdelists`, left-joining a four-field
`crsp_q_stock.stksecurityinfohdr` subquery on PERMNO without a date condition.
It restricts `DelistingDt` inclusively to **2019-11-01 through 2026-02-28**
and `DelActionType` to `GDR`, `GEX`, `GLI`, `LOS`, `MER`; there is no PERMNO
list or LIMIT. The local CSV SHA-256 is
`1495393c6f36a77c43cecb97f5433fdd3002fe27d1d979f44c78ecd22b1bd6cd`.
The 22 columns match SQL order, with 19 event-table and three Header fields.
The [CRSP delisting code table](https://www.crsp.org/wp-content/uploads/DelistCode.html)
supports the action names; code definitions alone do not prove complete
coverage in this export. The [CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf)
distinguishes recent Header values from dated History and describes delisting
and daily-return fields. A cached guide PDF was marked revised 2025-11-12;
the live URL currently redirects to a guide page marked effective 2026-07-31.
Neither guide date is this WRDS product's verified release vintage.

| Unit | Local observation | Meaning and denominator |
| --- | ---: | --- |
| Export rows | 4,092 | Rows returned by this filtered, joined local CSV |
| Distinct exported PERMNO | 4,092 | Normalized identifiers in those rows, not firms |
| Review groups | 4,092 | `(PERMNO, DelistingDt, DelActionType)` groups; no independently established event primary key |
| Full-row duplicate extras / same-event Header variants / review-key event conflicts | 0 / 0 / 0 | Observed groups or extra rows in this local export; source-event uniqueness and join multiplicity remain unverified |
| Starting securities | 7,639 | Previously bound 2020 starting PERMNO set; not an eligible M5 cohort |
| Starting securities with one / zero / multiple review groups | 2,203 / 5,436 / 0 | Denominator: 7,639 starting securities; zero means no event observed **within this query's date/action scope**, not survival |
| Export rows linked / not linked to starting securities | 2,203 / 1,889 | Denominator: 4,092 raw rows; unlinked rows remain in raw source and are not added to the initial set |

Action-row counts over the **4,092 export rows** are `GDR=945`, `GEX=87`,
`GLI=1,426`, `LOS=0`, and `MER=1,634`. A zero in one filtered export does not
prove that a source product or broader time range has no such events. No
observed duplicate pattern licenses a claim that the Header join is one-to-one.

`DelRet` is nonempty on **3,967/4,092 rows** and empty on **125/4,092**;
no return was calculated or compounded. `DelDlyDt` is nonempty on all 4,092
rows, but was not treated as equal to `DelistingDt` or computed by adding a
calendar day. The guide describes `DelistingDt` as the last price date on a
CRSP-followed exchange, `DelDlyDt` as the date a related return is stored in
daily data, and `DelRet` as a delisting total-return field. Those definitions
do not make `DelRet` the proposal's dividend-excluding price-return target.

The raw successor fields are preserved. Across the **4,092 export rows**,
`DelPERMNO` has 902 positive join-key candidates and 3,190 nonpositive numeric
values; `DelPERMCO` has 1,179 positive candidates and 2,913 nonpositive
numeric values. Among rows linked to starting securities, positive candidates
occur on 555 and 761 rows respectively. These are **unaccepted identity
candidates**, not continuity, price-splice or CIK decisions. The [CRSP SIZ-to-CIZ
conversion appendix](https://www.crsp.org/wp-content/uploads/appendix/FlagType_MU.html)
describes a minus-five convention for some historical successor identifiers, but the
complete missing/sentinel semantics of this exact WRDS export remain unverified;
nonpositive raw values were retained and excluded only from positive-ID joins.
The 3,194 rows with one or more successor-key review notes are not labeled
malformed delisting events; primary ID/date/action core issues were **zero**.

Among the 2,203 starting-linked event rows, 2,196 intersect one retained
security-history interval under the declared closed-date comparison; seven
have no event-date interval and no preceding retained interval. Those seven
were not assigned a future PERMCO. Relative to each security's last retained
history end, 2,140 event dates are earlier and 63 equal it; this is not an
independent delisting confirmation. Historical CUSIP changes occur in the
prior histories of 355 starting-linked event rows; no CIK link was accepted.
The previous issuer index listed 4,097 security intervals with multiple
issuer intersections. The new **separate derived view** classifies all 4,097
as sequential closed-date segments, with zero observed overlap, endpoint
touch or union gap under that convention. Multiple sequential intersections
are not automatically errors; this does not amend the earlier v3 index or
prove the provider's endpoint semantics.

All numbers above are from the ignored final
`artifacts/m5_delist_intake_20261111_v3/m5_delist_summary.json`
(SHA-256 `5223e9ff5c9a9f67884620e96fc9d49aa7d190554f39e5dd9d637b30e302b390`).
The private index, registry and file manifest SHA-256 values are, respectively,
`59e10dd0165c570b45c0913f90567c65d4915a4d7ed17d2a6fbccfb583377bea`,
`6940ee7896a1ed465e347e98cc2ed20cad0c63ccc5ff5f866c5a0a0f71734ff2`
and `333209acc538d21a41e5c6ba96b639e8157a61fd69ecf2d01c8322a3c7cca095`.
The [audit script](../scripts/audit_m5_delist_intake.py) SHA-256 is
`0a941c51ef51d5cec0a9bda2d526ae69ed1be15890a7f245678a4edb2857c847`.
Earlier ignored `v1`/`v2` directories are preserved as code-iteration
attempts; the current script binds `v3`.

## Daily-data acquisition gate

The next unreceived role is `StkDlySecurityData` for **the 7,639 previously
bound starting PERMNO identifiers**, generated locally in an ignored JSON
list (SHA-256
`bdba5c7c78ce30f3d86f5dc176083251b957624127f6c6ee924e5967a91b9cf6`).
The list does not include unaccepted successor candidates or select a formal
200-CIK sample. For the proposed **2019-11-01 through 2026-02-28** buffer,
there are 1,651 weekdays; 7,639 × 1,651 = **12,611,989 potential
security-by-weekday grid cells**. These are planning cells, not observed WRDS
rows, a provider size estimate, or an independently validated trading calendar.
Before requesting a large daily export, confirm the authenticated list-upload
mechanism, table/field spellings, batch/size limit, release and permitted
export. The required field list and subsequent `StkDistributions` role are in
the [updated download checklist](../docs/M5_WRDS_NEXT_DOWNLOAD.md). No daily
or distribution request was submitted here.

## Commands, provenance and remaining gates

The local commands used an explicit Python 3.11 executable and `PYTHONPATH=src`.
The exact private input paths and complete argument lists are retained only
in the ignored execution ledger. Terminal output from the audit commands
contained fixed status codes, never licensed rows or identifiers.

| Command | Exit | Observed result |
| --- | ---: | --- |
| `python -m pytest -q tests/test_audit_m5_delist_intake.py` (initial) | 1 | One synthetic SQL replacement assertion failed because test bytes used the wrong case; corrected without changing query semantics |
| `python -m pytest -q tests/test_audit_m5_delist_intake.py` (final) | 0 | 18 selected `SYNTHETIC_TEST_ONLY` tests passed; no real-data fixture, Encoder or model |
| `python scripts/audit_m5_delist_intake.py run ... --output-dir artifacts/m5_delist_intake_20261111_v1` | 0 | First local intake, preserved and code-stale |
| `python scripts/audit_m5_delist_intake.py run ... --output-dir artifacts/m5_delist_intake_20261111_v2` | 0 | Successor/interval semantics revision, preserved and code-stale |
| `python scripts/audit_m5_delist_intake.py run ... --output-dir artifacts/m5_delist_intake_20261111_v3` | 0 | `M5_DELIST_LOCAL_CANDIDATES_COMPLETE`; current source binding |
| `python scripts/audit_m5_delist_intake.py verify-output --output-dir artifacts/m5_delist_intake_20261111_v3` | 0 | `M5_DELIST_SAVED_OUTPUT_VERIFIED` |

The three local intake invocations read the same fixed CSV while software
review was improved; they are three engineering attempts, not separate
datasets or financial experiments. Originals and all previous receipts and
results remain unchanged. Intermediate selected synthetic suites passed 13,
15 and 17 tests as coverage was added; the final selected suite passed 18.
The ignored private command ledger records actual arguments and exits, with
invocation times explicitly unknown (SHA-256
`5b78d1278f4a9a2879a4563566ca2e7b4bdaba4b6e82a6dc2e13c4bca7585412`).
WRDS Query ID and page count, exact product release,
account/project export terms, complete action coverage, source event
uniqueness and Header join multiplicity remain pending. Historical CIK validity,
eligible market, primary share, text/price clock, exit treatment and the
price-return policy still need a separate researcher decision. S2/S3
engineering acceptance does not freeze M5. The colleague run package, JFE
paper identity and school GPU evidence remain separate unresolved items.

**Final gates:** `DELIST_QUERY_SPEC_VERIFIED`;
`LOCAL_DELIST_SOURCE_BOUND_REVIEW_ONLY`;
`DELIST_EVENT_AND_SUCCESSOR_CANDIDATES_UNACCEPTED`;
`JOIN_MULTIPLICITY_NOT_INDEPENDENTLY_VERIFIED`;
`PROVIDER_COMPLETENESS_NOT_VERIFIED`;
`HISTORICAL_CIK_LINK_VALIDITY_NOT_ESTABLISHED`;
`FORMAL_M5_NOT_FROZEN_NOT_RUN`;
`ENCODER_NOT_RUN / FINANCIAL_LABEL_NOT_CALCULATED /
TRAINING_NOT_RUN / PREDICTION_EVALUATION_NOT_RUN / BACKTEST_NOT_RUN`.
