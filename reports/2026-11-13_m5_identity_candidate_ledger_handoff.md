# M5 historical identity candidate ledger: local engineering handoff

**Date:** 2026-11-13. **Status:** `ENGINEERING_ONLY_CANDIDATE_IDENTITY`; `CANDIDATE_IDENTITY_LEDGER_BUILT_NOT_ADMITTED`. The [round-8 M5 proposal](../configs/m5_financial_protocol_round8.json) remains byte-identical (`f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38`), `PROPOSED / researcher_accepted=false / NOT_RUN`. This handoff does not select the proposed 200-CIK cohort or admit any financial observation.

## Plan, execution, evidence, limits, status

| Planned item | Actual execution | Saved evidence | Limit | Status |
| --- | --- | --- | --- | --- |
| Bind existing identity sources | The builder checked the six pinned prior registry/index/summary files, the registered raw-file hashes, the earlier intake-code hashes, the round-8 candidate and the conceptual ledger contract. It did not reparse the licensed CSVs or rerun earlier audits. | Private source manifest and registry; six prior artifact hashes in the safe summary; fixed-code commit `bac1e53530d25105a4b794f2338cb607ba254199` | Hash and saved-receipt agreement do not prove provider-universe completeness, release vintage, use/export scope or semantic validity of a link. | Complete for saved-source identity; semantic gate open |
| Preserve parallel candidates and exceptions | One retained starting issue/security-history interval became a node with separate CIK-CUSIP observation and issuer-history intersection edge lists. Later-only issue intervals and prior exceptions remain separate. | Ignored private ledger SHA-256 `49f00380da610f85637ea77178a2eda9f784e0ffe1ac9db2dee79a40dac4337f`; shareable safe summary SHA-256 `797510fcd085fe6f817387ce5791f33f5757900510cfc6af9b53017e7613c0e7` | No Cartesian CIK-by-issuer bridge, primary share, MIC or eligible class is asserted. | Built; not admitted |
| Separate time meanings | The ledger keeps security/issuer source-history dates, CIK first/last filing observations, the as-of snapshot reference and record-creation time in distinct fields. Unknown endpoints and source inclusivity remain unknown. | [Method and field mapping](../docs/M5_IDENTITY_CANDIDATE_LEDGER.md); [conceptual contract](../docs/M5_IDENTITY_LEDGER_CONTRACT_DRAFT.json) | Filing observation dates cannot be treated as CIK-link valid-from/valid-to. The saved closed-date issuer intersection is a diagnostic, not an approved canonical half-open interval. | Preserved; historical-validity gate open |
| Validate software and saved output | The selected synthetic suite passed **8/8** tests. One real offline build attempt exited 0 in 139.469 process wall seconds. A separate `verify-output` exited 0 in 20.103 process wall seconds after rehashing and recomputing saved relations, exceptions and counts. | Run status log SHA-256 `7055621e52299c3a04daeab2b145357f8797faa19353648eaddb745f18b79a2e`; verify status log SHA-256 `7dc72714af4b94247dcaf118f133f564142af330e8ab0a16d667b54c24b0d699`; output manifest SHA-256 `53e01b49ca603c61fee45485f9a8f87a93514caa20a712bef206c0e6287e9452` | Synthetic tests establish software behavior only. Peak memory was not measured (`UNKNOWN`). | Passed saved-output recomputation |
| Preserve research gate | All candidates remain unaccepted; primary security, MIC and eligibility are null. Formal cohort and admitted financial-observation counts are null. | Machine safe summary and private ledger status fields | An engineering candidate edge is not a historically valid filing-time mapping or an admitted sample. | M5 decision pending |

The actual interpreter was the verified local Python 3.11 installation, with this checkout's `src` explicitly set as `PYTHONPATH`; the observed `finembed.__file__` resolved to this checkout's `src/finembed/__init__.py`. The builder SHA-256 is `f482a41336271d0618e23d85a25e65d0bf87d40bbdfee6730d120aca7f83f5b2`. The logical handoff contract SHA-256 is `b6fe35194eac5bd74bbe47bbf9f295b5e1f439923dcbc2f796615bc8aa9d38c1`; it is a conceptual draft, not the executable output schema or a supplier data dictionary.

## Saved engineering counts and their denominators

These are **newly recomputed ledger counts from previously saved indexes**, not a fresh parse of raw WRDS rows. The safe summary records the prior receipts' raw row counts separately: security starting 7,639, security later 42,197, CIK-CUSIP 89,987, issuer early 108,964 and issuer later 31,951. Their unit is source rows in the prior receipts; this run only rehashed those registered source files. Unparsed/quarantined raw-row counts were not reassessed and remain `null`.

| Ledger unit | Count | Meaning and limit |
| --- | ---: | --- |
| Distinct starting issue IDs | 7,639 | Acquisition starting set, not eligible firms or a formal cohort. |
| Retained starting-issue history intervals | 29,404 | Nodes to which the parallel evidence edges attach. |
| CIK-CUSIP observation candidate edges | 47,125 | Exact historical CUSIP candidate rows, including parallel and duplicate evidence; no valid-time CIK bridge. |
| Issuer-history closed-date intersection candidate edges | 133,104 | Same-issuer comparison edges, not certified effective intervals. |
| Total parallel candidate edges | 180,229 | Sum of the two edge types, not three-way identities or independent companies. |
| Later-only issue intervals | 20,432 | Separate evidence concerning 6,922 distinct later-only issues; not added to the starting set. |
| Source-record locators retained | 92,810 | Referenced saved source rows, including exception-only rows; not all raw rows. |
| Starting intervals without a CIK observation candidate | 7,124 | Unmatched in the saved exact-match index, not proof of no real link. |
| Starting intervals without an issuer intersection candidate | 0 | All retained starting intervals have at least one saved diagnostic intersection; this does not validate its endpoint semantics. |
| Starting intervals with multiple distinct CIK candidates | 5,973 | Parallel candidates retained. |
| Starting intervals with multiple issuer intersections | 4,097 | Supplier endpoint meaning and interval conflicts still need review. |
| Exception records | 44,514 | Includes prior exceptions and new no-match exceptions; one record may have multiple codes. |
| Distinct affected source-record locators | 33,081 | Separate denominator from exception records and per-code incidences. |
| Exception records without a source-record subject | 814 | Aggregate or cross-interval evidence retained without inventing a row locator. |
| Intervals with null primary / MIC / eligibility | 29,404 each | No default was inferred from raw exchange, ticker, name or link quality. |
| Accepted candidates / formal cohort / admitted observations | `0 / null / null` | Zero accepted edges is a status; unknown cohort/observation counts are not zeros. |

All exception code counts, including duplicate rows and low-quality candidates, are in the safe machine summary. They overlap, so their sum is not an exception-record count. The full relation and exception rows remain in the ignored local ledger. The safe summary contains counts and hashes only; it contains no licensed row, security or company identifier.

## Commands and controlled scope

From the checkout root, `PYTHONPATH` was set to the absolute local `src` directory before every Python invocation. The shared PowerShell form of the selected software command was:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m pytest -q tests/test_build_m5_identity_candidate_ledger.py
```

It exited 0 with `8 passed in 1.45s`. The offline run and independent saved-output check were:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" scripts/build_m5_identity_candidate_ledger.py run --wrds-dir artifacts/m5_wrds_intake_20261111_v1 --issuer-dir artifacts/m5_issuer_history_20261111_v3 --output-dir artifacts/m5_identity_candidate_ledger_20261113_v1
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" scripts/build_m5_identity_candidate_ledger.py verify-output --output-dir artifacts/m5_identity_candidate_ledger_20261113_v1
```

Both used the pinned interpreter above and exited 0. The shared interpreter notation uses `$env:LOCALAPPDATA` to avoid a personal absolute path. The run wrote one new Git-ignored directory; verification read that saved output. No prior index, receipt, raw source, M5 proposal or other experiment artifact was overwritten. There was one actual build attempt and one separate saved-output verification. No Encoder session, new embedding, financial label, model fit, prediction score or backtest ran.

## Next evidence and researcher decisions

1. Establish a source-supported historical CIK-to-security effective-date bridge. `cikdate1`/`cikdate2` are filing observations, and the supplier's endpoint-inclusivity rule still needs evidence. Resolve parallel and conflicting candidates without accepting a high validation code by itself.
2. Establish issue and issuer history semantics, event-time market/MIC, common-share and unique-primary evidence. Preserve the later-only set and source conflicts. Verify provider-universe completeness, exact product vintage and project use/export scope separately from local file hashes.
3. Obtain a researcher decision on the formal cohort, multiple-class/ambiguous identity policy and the broader M5 time, price, exit and missingness rules. The [dated M5 decision sheet](../docs/M5_RESEARCHER_DECISIONS_20261112.md) keeps those choices separate from ordinary engineering work.
4. Keep the remaining Daily backend SQL/JOIN, Distribution server DAT/page association, duplicate-row cause and decision-time Item 1/public-close evidence gaps open in the [readiness matrix](../docs/M5_DATA_ADMISSION_READINESS_20261112.md). They were not reassessed by this identity build.

The source material is one bounded, historically acquired collection. Its candidate counts do not imply general identifier quality or financial feasibility. The colleague's missing run package, unidentified JFE paper and university GPU details remain separate historical gaps and do not change this engineering-only status.
