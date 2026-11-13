# M5 local issuer-history intake and interval candidates

**Scope:** `ENGINEERING_ONLY`. Two local CRSP issuer-history exports were bound
to their preserved SQL and to the previously bound security-history intake.
This report contains aggregate counts and source hashes, not licensed rows or
security/company identifiers. The [round-8 M5 candidate](../configs/m5_financial_protocol_round8.json)
retains SHA-256 `f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38`
and status `PROPOSED / researcher_accepted=false / NOT_RUN`. No formal cohort,
CIK link, delisting event or financial observation was admitted.

The starting checkout was clean at `5626b4c1d7544766e757747541b19926b8293473`
on `codex/research-plan-round10-20261106`. The explicit Python 3.11 executable
was used with this checkout's `src` on `PYTHONPATH`; `finembed` resolved to
this checkout. The new local script did not construct an Encoder.

## Plan, actual work, evidence, change, limitation and status

| Plan | Actual work | Evidence | Change or limitation | Status |
| --- | --- | --- | --- | --- |
| Locate the two new SQL/CSV pairs | Found the four exact files in the registered local location; retained raw bytes and earlier intake artifacts | File identities below and ignored registry | No broad personal-file search or new WRDS request | DONE |
| Bind query definitions | Rehashed and parsed both SQLs: one source table `crsp_q_stock.stkissuerinfohist`, the same 18 selected fields, only the two stated `IssInfoStartDt` ranges, no code list, join or limit | SQL hashes below; exact parser in [source](../scripts/audit_m5_issuer_history.py) | The proposed initial-PERMCO-list request became two unfiltered start-date segments through 2026-06-30. This is an acquisition-scope change, not an M5 cohort/event-year change | `ISSUER_QUERY_SPEC_VERIFIED` |
| Check both local CSVs | Rehashed before and after read; verified ordered 18-column headers, row widths, positive normalized PERMCO, parsed start/end dates, row interval direction and each source's start-date filter | Private registry plus aggregate result below | WRDS Query IDs, server result counts, retrieval times, exact release and project export terms were not supplied. Local counts cannot reconcile a provider page or prove full-universe coverage | `LOCAL_ISSUER_CSV_STRUCTURE_AND_HASH_BOUND` |
| Bind prior security identity | Rechecked pinned prior registry/index/summary hashes, the three prior source/receipt hashes and prior script identity; recovered PERMCO from each retained security source row and checked its prior row digest/date/identifier | Prior artifact hashes below; source and private index | The earlier v2 audit was not rerun. Later-only securities stay outside the starting set | DONE |
| Inventory dated issuer/security associations | Kept all original issuer rows and private source-record references; associated identical PERMCO only where raw effective-date ranges intersect; retained all zero/one/multiple matches and closed-date coverage diagnostics | Final private candidate index SHA-256 and machine summary below | Date intersection uses an explicitly labeled closed-date comparison. The official guide identifies the effective-range fields but did not establish endpoint inclusivity. A match is not a historical CIK validation | `UNACCEPTED_CANDIDATES` |
| Read back saved output | Rehashed source SQL/CSVs, previous artifacts and saved index/registry; checked aggregate counts and nested `accepted=false` flags | `verify-output` returned `M5_ISSUER_HISTORY_SAVED_OUTPUT_VERIFIED`, exit 0 | Hash consistency does not authenticate provider completeness or permission | PASS |

## Source and count ledger

The first SQL uses `IssInfoStartDt BETWEEN 1925-12-31 AND 2020-01-01`;
the second uses `2020-01-02 AND 2026-06-30`. Their source-row effective
range is `IssInfoStartDt`–`IssInfoEndDt`; `IssuerBegDt`/`IssuerEndDt` were not
substituted. The early row's actual end is not clipped at 2020-01-01.
The [CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf)
defines the distinct security and issuer history roles and `CNUM` as an issuer
CUSIP component. A CUSIP prefix or name is not used to create a CIK link.

| Role | SQL SHA-256 | Local CSV SHA-256 | Local rows | Columns |
| --- | --- | --- | ---: | ---: |
| Early issuer start dates | `2846728fa21637dda76406f14f2540cdaa100f5bd70062ccb3aee781eaa651e6` | `368c7cd66dcb645163f14c2663556f3ded4f29b3ad8a87354c95659821ba1007` | 108,964 | 18 |
| Later issuer start dates | `7f234d4338b6674de35b646e15577e511c7ce268137e852052702231d9d2c0b6` | `10e956a8c5a4b11ae028adb7ccab9ed7fe0699e7d2a4460a0a7375a0ed825398` | 31,951 | 18 |

The two raw files contain **140,915 issuer-history rows** and **33,458 distinct
normalized PERMCO identifiers** across those rows. Neither number is a formal
company/sample count. The prior starting file's 7,639 distinct security IDs
map to **5,590 distinct initial PERMCO identifiers**. The retained starting
securities have **29,404 security-history intervals**; this local mapping found
zero later PERMCO-change candidates among those retained intervals. This says
nothing about identity continuity outside the acquired rows.

Of the 140,915 issuer rows, 45,067 have a PERMCO in the related identifier set;
95,848 other full-library rows remain in the raw files and were not added to
the starting set. Under the closed-date comparison, the 29,404 retained
security intervals have 25,307 with one intersecting issuer row, 4,097 with
multiple rows, zero with none, and zero without full date coverage. There are
133,104 **interval-to-issuer candidate edges**, not independent companies or
admitted observations. The private exception index has 4,097 records for
multiple intersections; this local scan observed no exact duplicate issuer
rows, same-range attribute conflicts, issuer-to-issuer range overlaps/gaps, or
issuer-to-issuer endpoint touches. The last observed `IssInfoEndDt` is before 2025-12-31 for 1,599
related PERMCO identifiers; this is **not** a delisting count.

All counts above come from the ignored
`artifacts/m5_issuer_history_20261111_v3/m5_issuer_history_summary.json`
(SHA-256 `db71f33d7cc662099703100f4f5c38dd68a77234f352b1b7ca5d4febe96fef75`).
Its denominators are raw issuer rows, distinct identifiers, retained starting
security intervals, or interval-to-issuer edges as individually labeled.
The private index SHA-256 is
`370318027c222965de32467641c00998e5c3eb985344db41891807c4e6a0a833`;
the private registry SHA-256 is
`713f7deeb6aa43b5e43b62482e3c36755c49123b5b28c9f63b36c7d822be6194`.
Those files remain Git-ignored and contain private paths or row-level data.

The prior three-file registry, candidate index and summary retain SHA-256
`8482df5d3dfccc72e5513179aa1b91f0847321488c0c1c9161f95d8e10cf08ec`,
`de8df60b998d94d76a8452685bb94b27aa68ff864b5850a796b612824d3895bf`
and `9d33b02743f3d7683462a286e0f25221c957ad00e75788152817091712ef6ebe`.
The new script SHA-256 is
`d7716b1c49f4647b6ad45168242247e7e2d3fda6cffbc5bb0c2c7f32e4cb2729`.
Earlier ignored issuer directories `v1` and `v2` are preserved as software
iteration attempts; the current code binds only `v3`.

## Actual local commands and checks

The commands below used the explicitly selected local Python 3.11 interpreter
after setting `PYTHONPATH` to this checkout's `src`. Exact interpreter and input
paths are retained only in the ignored registry. Inputs were passed
locally and are omitted from this shareable report. No licensed row or
identifier appeared in terminal output.

| Command | Exit | Observed result |
| --- | ---: | --- |
| `python -m pytest -q tests/test_audit_m5_issuer_history.py` | 0 | 16 selected `SYNTHETIC_TEST_ONLY` tests passed; no real CRSP fixture or Encoder |
| `python scripts/audit_m5_issuer_history.py run ... --output-dir artifacts/m5_issuer_history_20261111_v1` | 0 | Initial local candidate output; preserved, now code-stale |
| `python scripts/audit_m5_issuer_history.py run ... --output-dir artifacts/m5_issuer_history_20261111_v2` | 0 | Revised count labels; preserved, now code-stale |
| `python scripts/audit_m5_issuer_history.py run --early-csv ... --early-sql ... --later-csv ... --later-sql ... --prior-dir ... --output-dir artifacts/m5_issuer_history_20261111_v3` | 0 | `M5_ISSUER_HISTORY_LOCAL_CANDIDATES_COMPLETE`; current source binding |
| `python scripts/audit_m5_issuer_history.py verify-output --output-dir artifacts/m5_issuer_history_20261111_v3` | 0 | `M5_ISSUER_HISTORY_SAVED_OUTPUT_VERIFIED` |

The three intake invocations processed the same fixed local files while the
software verifier was tightened; they are three engineering audit attempts,
not three samples or financial experiments. No original file, prior receipt,
S2/S3 result or M5 candidate was overwritten.
The ignored private execution ledger records exact local arguments and exit
codes (SHA-256 `7b1c5f20d3709eb47ab2d2685d260319436ee2cc67394f430c9c352344cadc62`);
invocation timestamps were not recorded at call time and are not invented.

## Next data and decision gates

The next unobtained raw role is CRSP `StkDelists`, followed by dated
`StkDlySecurityData` and `StkDistributions`; the precise proposed fields and
buffers are in the [download checklist](../docs/M5_WRDS_NEXT_DOWNLOAD.md),
subject to the authenticated WRDS dictionary, product release, permission and
export terms. Obtain separate official or product evidence for historical CIK
link validity, a documented primary-security choice, applicable market
calendar, exact Item 1 text version and publication time, and closing-price
availability. Do not treat first/last CIK filing observations as effective
dates, issuer history end as a delisting, or a quality code as acceptance.

The researcher's unresolved scientific choices remain: strict historical-as-of
versus fixed-snapshot retrospective interpretation; eligible markets and
primary security; date-valid CIK bridge; text and price decision clocks;
and terminal-price, action and return treatment. The colleague run package,
JFE paper identity and university GPU evidence remain separate unresolved
items. No WRDS account was probed and no new provider request was made.

**Final gates:** `ISSUER_QUERY_SPEC_VERIFIED`;
`LOCAL_ISSUER_CSV_STRUCTURE_AND_HASH_BOUND`;
`ISSUER_CANDIDATES_UNACCEPTED`;
`PROVIDER_UNIVERSE_COMPLETENESS_NOT_VERIFIED`;
`HISTORICAL_CIK_LINK_VALIDITY_NOT_ESTABLISHED`;
`FORMAL_M5_NOT_FROZEN_NOT_RUN`;
`ENCODER_NOT_RUN / FINANCIAL_TRAINING_NOT_RUN /
PREDICTION_EVALUATION_NOT_RUN / BACKTEST_NOT_RUN`.
