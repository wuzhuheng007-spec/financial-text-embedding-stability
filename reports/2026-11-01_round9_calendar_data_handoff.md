# Round 9: independent calendar and historical-data import readiness

**Status:** `CALENDAR_BUILT_AND_VERIFIED / PRICE_COVERAGE_BASELINE_ONLY / REAL_IMPORTS=0 / M5_PROPOSED / FINANCIAL_EXPERIMENT_NOT_RUN`. The unchanged [round-8 M5 candidate](../configs/m5_financial_protocol_round8.json) has SHA-256 `f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38` and remains `PROPOSED`, `researcher_accepted=false`, `NOT_RUN`. This round calculated no return label, fitted predictor, prediction score, position, or backtest. Saved S2/S3 results remain bounded engineering evidence, not a financial baseline.

## Plan, execution, evidence, deviation, status

| Planned item | Actual execution | Evidence | Deviation or limit | Status |
| --- | --- | --- | --- | --- |
| Preserve the round-8 M5 proposal and establish an isolated round-9 branch | Started at `8bf5db8d290d9af61488bdf1747b853219d79100` on `codex/research-plan-round9-20261101`; the round-8 candidate byte hash above was rechecked | Git HEAD and candidate SHA-256; no M5 acceptance record | Final local commit is recorded separately after staged review | DONE |
| Build an independent regular-session calendar with boundary buffers | [Official-source rules](../configs/nyse_calendar_rules_round9.json) generated 1,588 XNYS core-equity sessions, 63 closures and 14 early closes for the 2019-11-01 through 2026-02-28 rule range; the last actual session is 2026-02-27. Saved rows and all source hashes were rebuilt and verified | Local `artifacts/round9/nyse_calendar_v2/{manifest.json,sessions.csv,closures.csv}`; hashes below | This is a retrospective NYSE benchmark; historical listing MIC for each development security is unverified | DONE, MARKET-SCOPE LIMITED |
| Compare saved 2025 bars with the independent calendar at horizons 5 and 21 | All 250 saved common dates matched the 250 XNYS 2025 session dates; zero dates were present on only one side. Both horizons had 5/5 baseline structural pairs; 4/5 also had the required predecision close window | Local `artifacts/round9/m5_xnys_coverage_v2.json`; per-firm endpoints below | No security-specific calendar confirmation or admitted financial observation; META lacks the saved predecision lookback | DONE, STRUCTURAL ONLY |
| Specify future authorized historical-data ingestion | Added [canonical import contract](../docs/M5_HISTORICAL_IMPORT_ROUND9.md), source adapter requirements and [structure validator](../scripts/validate_m5_import_round9.py); validated synthetic issue, firm, price, exit and text-evidence cases | Selected software tests and ignored logs below | No authorized historical vendor file was present or imported; fixture rows are `SYNTHETIC_TEST_ONLY` | DONE, SPECIFICATION ONLY |
| Prepare a one-page access and decision request | Added [faculty/librarian confirmation sheet](../docs/M5_DATA_ACCESS_CONFIRMATION_ROUND9.md) with distinct WRDS and UCLA access descriptions | Direct official links in the sheet and below | The sheet has not been sent; eligibility, subscriptions and export terms remain unknown | DONE, NOT SENT |
| Validate without financial execution | Explicit four-file pure-software pytest command passed 35 tests; calendar and coverage readback verifiers passed | `artifacts/round9/selected_tests_v2.log` and v2 machine receipts | A narrow validator correction added `NO_PRIMARY_SECURITY` for a single or interval-gap link without a unique primary, followed by an extra selected test and the final rerun. No unfiltered pytest suite, Encoder, financial fit, score or backtest | DONE |

## Calendar identity, source versions and scope

The [rules file](../configs/nyse_calendar_rules_round9.json) binds each holiday, early close and the 2025-01-09 temporary closure to an official source ID, URL, announcement date when stated, retrieval UTC and raw-file SHA-256. The source originals and `artifacts/round9/calendar_sources/source_manifest_v3.json` remain in the ignored local directory. The [NYSE core-session page](https://www.nyse.com/trade/trading-information) states 09:30–16:00 Eastern Time; the builder records America/New_York open and close instants and corresponding UTC instants. The live hours page does not state its original publication date, so it does not by itself prove a point-in-time historical hours version.

The applicable official schedules are the directly opened raw PDFs: [2018 ICE 2019–2021 notice](https://s2.q4cdn.com/154085107/files/doc_news/archive/15638cee-2935-4422-bcb4-a3733587eb8b.pdf), [2019 ICE 2020–2022 notice](https://s2.q4cdn.com/154085107/files/doc_news/archive/91a5230f-7233-4a93-b30a-79c9411ebdbe.pdf), [2021 ICE 2022–2024 notice](https://s2.q4cdn.com/154085107/files/doc_news/NYSE-Group-Announces-2022-2023-and-2024-Holiday-and-Early-Closings-Calendar-2021.pdf), [2022 ICE 2023–2025 notice](https://s2.q4cdn.com/154085107/files/doc_news/NYSE-Group-Announces-2023-2024-and-2025-Holiday-and-Early-Closings-Calendar-2022.pdf), [2023 ICE 2024–2026 notice](https://s2.q4cdn.com/154085107/files/doc_news/NYSE-Group-Announces-2024-2025-and-2026-Holiday-and-Early-Closings-Calendar-2023.pdf) and [2024 ICE 2025–2027 notice](https://s2.q4cdn.com/154085107/files/doc_news/NYSE-Group-Announces-2025-2026-and-2027-Holiday-and-Early-Closings-Calendar-2024.pdf). Their source IDs and exact retrieved bytes are in the rules and v2 manifest. In particular, the 2019 notice did not yet include the 2022 Juneteenth closure; the later 2021 notice did. The 2024 annual schedule did not include the 2025-01-09 closure; the [separate ICE/NYSE Carter PDF dated 2024-12-30](https://s2.q4cdn.com/154085107/files/doc_news/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of--IGCRS.pdf) supplies that temporary exception. Annual scheduled and later exception versions are not silently merged into a claim about what was known at every earlier decision time.

| Year | Saved XNYS sessions | Coverage meaning |
| --- | ---: | --- |
| 2019 | 41 | Partial buffer, 2019-11-01 through year-end |
| 2020 | 253 | Full core year |
| 2021 | 252 | Full core year |
| 2022 | 251 | Full core year; includes the subsequently announced Juneteenth observance |
| 2023 | 250 | Full core year |
| 2024 | 252 | Full core year |
| 2025 | 250 | Full core year; includes the 2025-01-09 temporary closure |
| 2026 | 39 | Partial buffer, through 2026-02-27, the last session before the 2026-02-28 range end |

The 2019 buffer supplies 42 close positions through the first 2020 decision session, 2020-01-02. The 2026 buffer supplies 38 *subsequent* sessions after the first post-2025 decision session, 2026-01-02. Both exceed the candidate's 21-session historical and alternative future windows. The generated calendar describes retrospectively observed sessions; the announcement vintage for every future decision and temporary schedule update is a separate evidence question.

An [official Nasdaq 2025 trading calendar](https://www.nasdaqtrader.com/content/technicalsupport/2025tradingcalendar.pdf) was visually checked against the planned 2025 holidays and 13:00 early closes; Nasdaq [separately announced its 2025-01-09 closure](https://www.nasdaq.com/press-release/nasdaq-announces-closure-its-us-markets-honor-national-day-mourning-former-president). An older Nasdaq holiday-hours PDF URL redirected to a **2026** PDF when fetched; that response is retained in the superseded ignored source manifest v2 and excluded from the effective v3 evidence. Nasdaq agreement for these 2025 dates does not certify all years or the listing market of a particular security. The saved five-firm identity records do not provide a historical listing MIC; the XNYS axis below is therefore a **benchmark comparison**, not a security-specific confirmed calendar.

The first saved calendar/coverage outputs, `nyse_calendar_v1` and `m5_xnys_coverage_v1.json`, remain local. A narrow builder correction added closure-`kind` enumeration validation and a curated official-date test; v2 was rebuilt and is the final bound version. This changed the builder and manifest hashes but **not** the `sessions.csv` or `closures.csv` bytes, whose v1/v2 hashes match. No old receipt was edited.

## Five-firm 5/21 reference-axis coverage

The audit uses the unchanged candidate's horizon values and the saved 2025 dates, without calculating prices or returns. The first regular XNYS session **after** each official filing date is `d`; endpoints are the fixed `d+5` and `d+21` sessions. A missing price or session row is reported against the declared endpoint and cannot move it. All ten baseline comparisons had zero endpoint differences from round 8's saved-row pairing. This agreement does not prove that any ticker's historical venue was XNYS or that filing text and a finalized close were available at the candidate's decision clock.

| Development filing | Official filing date | XNYS baseline `d` | `d+5` | `d+21` | Saved 21-close predecision window | Security-specific pair |
| --- | --- | --- | --- | --- | --- | --- |
| AAPL `0000320193-25-000079` | 2025-10-31 | 2025-11-03 | 2025-11-10 | 2025-12-03 | Present | `null`: historical MIC unverified |
| MSFT `0000950170-25-100235` | 2025-07-30 | 2025-07-31 | 2025-08-07 | 2025-08-29 | Present | `null`: historical MIC unverified |
| AMZN `0001018724-25-000004` | 2025-02-07 | 2025-02-10 | 2025-02-18 | 2025-03-12 | Present | `null`: historical MIC unverified |
| GOOGL `0001652044-25-000014` | 2025-02-05 | 2025-02-06 | 2025-02-13 | 2025-03-10 | Present | `null`: historical MIC unverified |
| META `0001326801-25-000017` | 2025-01-30 | 2025-01-31 | 2025-02-07 | 2025-03-04 | **Absent** in saved 2025 bars | `null`: historical MIC unverified |

The machine coverage receipt reports `xnys_baseline_complete_pairs=5` and `xnys_baseline_pairs_with_predecision_lookback=4` **for each horizon**; `security_calendar_confirmed_pairs=null` and `admitted_financial_observations=null`. It compared 250 saved 2025 common dates with 250 XNYS 2025 sessions and found neither a saved date outside XNYS nor an XNYS date lacking a saved common bar. These are date and saved-row facts. The five texts are development material and do not create a 2020 cohort, independent multi-filing sample, 2020–2025 price history, dividend/exit policy, or approved financial observation.

## Historical import and research gates

The [proposed staging contract](../docs/M5_HISTORICAL_IMPORT_ROUND9.md) requires an actual product/version adapter and raw-file hash before mapping a historical security master, effective-dated CIK-to-security links, daily closes and corporate actions, exit records, and versioned original/amended text with public-time evidence. `valid_from` is inclusive and `valid_to` exclusive. Overlaps, multiple primary share claims, no unique primary on a covered date (`NO_PRIMARY_SECURITY`), absent required price sessions, duplicate or conflicting bars, unknown exit terminal values and unsupported publication-time claims are **reported**, not resolved by picking a convenient security, advancing a label endpoint, filling zero or dropping an exited issue. A synthetic validator pass only checks canonical structure. Real authorized files inspected/imported this round: **0**.

The [one-page confirmation sheet](../docs/M5_DATA_ACCESS_CONFIRMATION_ROUND9.md) asks a supervisor or librarian to identify the exact historical products, coverage, licensed project use/export, historical identifiers and an eligible supervised account route. [WRDS Account Types](https://wrds-www.wharton.upenn.edu/pages/about/wrds-account-types/) describes research-assistant accounts for graduate and undergraduate students researching for faculty at the same subscribing institution; [UCLA Anderson's database page](https://www.anderson.ucla.edu/rosenfeld-library/databases/business-databases-by-name) states that individual undergraduate UCLA WRDS accounts do not qualify and that access is at the UCLA representative's discretion. Those statements have different scopes and do not establish the user's eligibility, subscribed products or permission to export this project's data. The sheet has not been sent.

M5 remains blocked on a historical 2020 security universe and link vintage, 2020–2025 plus boundary market/actions/exit history, a verified listing-market calendar for each security, Item 1 text/revision and dissemination evidence, the close-availability clock, and the researcher decisions on cohort, target, claim regime, decision time, primary-share rule, exit treatment and final evaluation policy. Obtaining the data fields does not itself freeze the protocol. The existing split-adjusted saved 2025 bars do not contain the required dividend-inclusive or exit history. The colleague's small-step package remains not located, the JFE paper remains unidentified, and university GPU allocation information remains unverified; they are separate gaps. The round-3 accidental real-Encoder synthetic-text tests and the transient private quote in an unpushed round-4 intermediate commit remain historical deviations; no quote is repeated and no Git history was rewritten.

## Commands, verification and bound identities

Commands below ran in the round-9 checkout using the verified Python 3.11 environment and `PYTHONPATH` explicitly set to the checkout's `src`. `<protected-M4-run>` denotes an existing local read-only input whose private absolute path is intentionally omitted. Build commands used a new ignored output path; no previous run was overwritten.

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
python -m pytest -q tests/test_build_nyse_calendar_round9.py tests/test_audit_m5_calendar_coverage_round9.py tests/test_validate_m5_import_round9.py tests/test_audit_m5_horizon_round8.py
# exit 0: 35 passed in 1.10s; artifacts/round9/selected_tests_v2.log
python scripts/build_nyse_calendar_round9.py --rules configs/nyse_calendar_rules_round9.json --output-dir artifacts/round9/nyse_calendar_v2
# exit 0: 1,588 sessions; 63 closures; 14 early closes
python scripts/build_nyse_calendar_round9.py --rules configs/nyse_calendar_rules_round9.json --verify-dir artifacts/round9/nyse_calendar_v2
# exit 0: PASS_SOURCE_BOUND_CALENDAR_REBUILT
python scripts/audit_m5_calendar_coverage_round9.py --candidate configs/m5_financial_protocol_round8.json --round8-receipt artifacts/round8/m5_horizon_coverage_v5.json --data-audit artifacts/round7/m5_data_audit_v5.json --rules configs/nyse_calendar_rules_round9.json --calendar-dir artifacts/round9/nyse_calendar_v2 --m4-run <protected-M4-run> --output artifacts/round9/m5_xnys_coverage_v2.json
# exit 0: XNYS_BASELINE_ONLY h=5:5/5 h=21:5/5
python scripts/audit_m5_calendar_coverage_round9.py --candidate configs/m5_financial_protocol_round8.json --round8-receipt artifacts/round8/m5_horizon_coverage_v5.json --data-audit artifacts/round7/m5_data_audit_v5.json --rules configs/nyse_calendar_rules_round9.json --calendar-dir artifacts/round9/nyse_calendar_v2 --m4-run <protected-M4-run> --verify-output artifacts/round9/m5_xnys_coverage_v2.json
# exit 0: PASS_XNYS_BASELINE_COVERAGE_RECOMPUTED
```

The focused tests cover weekend and scheduled/temporary closures, early closes, DST ET/UTC conversion, source or saved-row tampering, buffer and invalid-rule rejection, fixed 5/21 endpoints with missing prices, historical effective-date overlap, missing or multiple primary securities, duplicate bars, exit-value uncertainty and text-publication evidence. Their canonical fixtures are `SYNTHETIC_TEST_ONLY`; no real vendor file, financial label or model score follows from these passes. The earlier 34-test selected log and narrower 12-test import-validator log remain local correction history; they are not additional independent samples or additive totals.

| Bound file | SHA-256 |
| --- | --- |
| Unchanged M5 candidate | `f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38` |
| Tracked NYSE rules | `0f5922dded516a61507ea24762d2a367b31b2798c95e49f55ae4230cf4e4d17a` |
| Calendar builder | `b5eb51c66e1c7c4e5576c4d19aa5ad2bb9c3644f16af45d440daf11bc63c2925` |
| Calendar manifest / sessions / closures | `ebd95c7f293088545706a0fac65c8c25427748a0e1f01ded88b3ee950c0d3565` / `2072e81065f8749fb41f531f8c62338d0ff478a2965e00ced18ee324d995489c` / `64040394be1f481dc1dc4d70ac18fb7aa2ec1841e768d58cc68f6d4ac8b54b5a` |
| Coverage audit code / saved receipt | `570f26b843a823e37c0b4b8ecec1cb32efb3d037e4341b037ea5b01534384429` / `69664fe4b82efe8281b3d0c7e041ac29c46c3c347cc4cd7702e5e202235acc86` |
| Import validator code | `2b771961ae7a03948eff369772407117995c724b9a64cc88a24a9e6d67cd640c` |
| Effective official source manifest v3 | `542f0504fe2f376245d83a6f8755580fefceddde2ed7db33426a33d44d5a2f02` |
| Selected 35-test log | `97e9b141515ed8ac2a05d5bc13268de413bca6cc635e449a45db8edfbdc561be` |

The v2 calendar verifier reconstructs and compares the saved rows, rule/source identities and file hashes; the coverage verifier recomputes its receipt from the saved calendar, candidate and retained date evidence. Source PDFs/HTML, raw market data, original filing text, vectors, weights, credentials and private paths remain local and ignored. The shared handoff must contain only deidentified summaries and hashes. No Encoder, financial training, prediction evaluation, backtest, label calculation or new financial observation was run in this round.
