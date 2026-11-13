# R2: real SEC filing candidate discovery

## Plan and actual execution

| Planned action | Actual result | Evidence |
| --- | --- | --- |
| Freeze a bounded CIK scan from the saved fixed-start screen | Selected 32 nondevelopment CIKs by SHA-256 rank of `m5_discovery_20261114_v1:` plus CIK, restricted to `CANDIDATE_SCREEN_PASS`; five development CIKs excluded | Private scope manifest SHA-256 `b3b157e77dc52e8d65568682504a9762e6455ee88d5b43a6a12794ca101f1276` |
| Query official SEC submissions metadata for original 10-K filings dated 2020-01-01 through 2025-12-31 | 32/32 main JSON and 5/5 in-window historical JSON succeeded; all 32 window scans complete. Registered 133 nondevelopment original 10-K accession rows from 26 CIKs. The prior five-CIK development inventory contributed 30 separate `development_only` rows | `candidate_discovery_summary_safe.json`; private source-hashed ledger |
| Freeze and fetch at most 20 originals | Stable sort by filing date, CIK, accession selected 20; 20/20 complete SEC submissions downloaded and SHA-256 registered. Eighteen passed unique filer/header identity and date checks; two have two `FILER` blocks and remain ambiguous | Private download selection manifest SHA-256 `1cab1efccdf8bedea3a83468ce292393de6774fd8747643778b6e47b633903de` and private download log |
| Extract candidate Item 1 with source-exact boundaries | Sixteen of the 18 header-supported originals passed exact substring and byte-hash checks: 11 base headings, two headings with trailing punctuation, three standalone `Item 1.` followed by `Business`. Two further boundaries remain ambiguous. Two observed fallback layouts received manual start/end checks. These are candidate texts, not formal text admissions | `candidate_discovery_verify_safe.json`; private boundary review |
| Verify provenance and separation | All 133 nondevelopment metadata rows were reread at their SEC array offsets against 28 distinct cached JSON bodies and SHA-256 hashes. All 20 original submission bodies and 16 Item 1 files were rehashed; each extracted text was checked against its source-rendered character offsets. Thirty development rows were reread against their previously hash-verified inventory. Six synthetic discovery tests passed | `candidate_discovery_verify_safe.json`; `tests/test_build_m5_candidate_discovery_20261114.py` |

## Scope and unresolved fields

This is a bounded exploratory scan of 32 screened CIKs, not an exhaustive starting-universe or formal cohort count. The saved screen is a candidate CIK/security relation with observation dates, not established historical validity. Every event keeps `security_id=null`, `formal_admitted=false`, and distinct identity, text, calendar, price, and exit statuses. For undownloaded filings, original body and Item 1 hashes stay null. All event-time issue impacts remain unknown until a supported historical security join and saved-source `LINKENDDT` interpretation exist. Formal M5 returns, signals, models, and financial evaluation are **NOT_RUN**.

The private ledger SHA-256 is `eea1a6470e250438d30a0f3d63101e15d2ea973ef4b1999dbe85fcd5295320ea`. The screened candidate source SHA-256 is `2254c697a3a0a3df0edac16b403a7737f0285f9f6735d06e5a37595bcd212602`; the prior development inventory SHA-256 is `dea6d376e1f613cf968de09d75347fe3b0ae9a3919260d4153c05493f34ac76b`. All private ledgers, candidate security identifiers, SEC cache bodies, and extracted text stay outside the shareable return ZIP.

## Reproduce and verify

Run from the repository root in PowerShell. A new discovery run requires a real identifying `SEC_USER_AGENT` in the process environment; its value is never written to the ledger. The new output directory must not already contain a frozen scope.

```powershell
python -m unittest tests.test_build_m5_candidate_discovery_20261114 -v
python scripts/build_m5_candidate_discovery_20261114.py freeze --out artifacts/m5_next_replay_local
python scripts/build_m5_candidate_discovery_20261114.py scan --out artifacts/m5_next_replay_local
python scripts/build_m5_candidate_discovery_20261114.py download --out artifacts/m5_next_replay_local
python scripts/verify_m5_candidate_discovery_20261114.py --out artifacts/m5_next_replay_local
```

Verify this completed run without refetching:

```powershell
python scripts/verify_m5_candidate_discovery_20261114.py --out artifacts/m5_next_20261114_0325
```

Progress: `██████████` R2 bounded candidate discovery and verification complete; formal M5 admission and financial experiment `NOT_RUN`.
