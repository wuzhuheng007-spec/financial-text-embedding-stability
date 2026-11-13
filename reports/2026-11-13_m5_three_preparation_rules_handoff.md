# M5 accepted preparation rules: local engineering execution

**Date:** 2026-11-13. **Scope:** `ENGINEERING_ONLY_PREPARATION`. **Execution-code commit:** `35cbe9b40cea3c493771c862d8a9e1ca31230713`. The user-transferred handoff ZIP passed CRC and its ten listed payload hashes; its SHA-256 is `63040ad7a893b9edf99cfbe7ac291bab3d17daa46982f70e1a23bd095efaeafb`. The three preparation rules are recorded in the [accepted policy](../configs/m5_preparation_rules_accepted_20261113.json), SHA-256 `14f22ae821d61d44848832fc5e73b5b27360f5947cafb4189ceb27603a38970e`. The separate ignored local decision receipt has SHA-256 `cf701cb379b6849ceb5f89def2c7e38f3e9b2d16f6a1ac7971b032201bb8c56c`. The packet author reported seeing the direct user reply; this local Codex run received it through the handoff. The original message's exact UTC time is unknown (`null`), and neither the packet time nor local receipt time replaces it. A file hash does not authenticate a person.

The unchanged round-8 candidate is SHA-256 `f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38`, `PROPOSED / researcher_accepted=false / NOT_RUN`. The previously accepted scope-A direction remains a retrospective fixed-byte interpretation. Acceptance of the three preparation rules does not admit a historical identity, a formal cohort, an event mask or a financial observation.

The [shareable run and verification record](m5_preparation_run_verification_safe_20261113.json) keeps the executed commands, process-wall measurements, safe-log hashes and private-output **hashes only** without copying private files. Both original safe machine summaries are included byte-for-byte in the sanitized handoff ZIP.

## Plan, actual work, evidence, limits, status

| Plan | Actual work | Saved evidence | Limit | Status |
| --- | --- | --- | --- | --- |
| Register the three decisions independently of full M5 | A Git-ignored receipt binds the transferred reply, proposal snapshot and public policy. The public policy keeps `preparation_rules_researcher_accepted=true` and `full_m5_protocol_researcher_accepted=false`. | Policy and private receipt hashes above; source-code commit | Message time is not independently available and software does not certify the decision maker. | `ACCEPTED_PREPARATION_ONLY` |
| Screen the pinned identity ledger | A new derived view retained every CIK observation edge, its original quality code and source-issue codes. Only an existing exact historical CUSIP8 edge with quality code 2/3 and a normalized nonempty CIK passed the preparation screen. Parallel links remained distinct. | Candidate safe summary SHA-256 `1f9c62a27884162302e6b40acefd9898bb81cb00a371a06a6e54b067d70d56b0`; private view file SHA-256 `2254c697a3a0a3df0edac16b403a7737f0285f9f6735d06e5a37595bcd212602` | The old export predicate, continuous CIK-to-issue validity, primary issue and MIC remain unverified. A screened or unique candidate is not accepted identity. | `CANDIDATE_SCREEN_BUILT_NOT_ADMITTED`; saved output verified |
| Preserve the 2020 acquisition scope | The bound starting securities and later-only securities were counted separately; eligibility, market/MIC and primary-security values remain unknown. | Candidate summary and pinned prior identity ledger SHA-256 `49f00380da610f85637ea77178a2eda9f784e0ffe1ac9db2dee79a40dac4337f` | Neither count is an eligible company cohort; no future price coverage selected membership. | Engineering scope retained; formal cohort `null` |
| Derive Daily rows and diagnose availability | The 2019 segment was read once with the prior parser's logical row/key definition; the other seven segments reused bound full-parsed-field key indexes and exceptions. Every raw logical row maps to a saved private SQLite record. Equal-key groups use stable first-source-row representatives; conflicting or unproven groups would be quarantined. The independent saved-output verifier recomputed key groups, dispositions, mapping fingerprint, field-presence counts and null gates. | Daily safe summary SHA-256 `07fe5604e8d80434559f74be2a3383715c72598a7d57ae4229c04f0d9d4959e2`; private SQLite SHA-256 `9625bf39efda1156b4ed92ccc3bcc997d14f1ff1369416334216da894db80321` | Equality of parsed rows does not explain the supplier duplicate cause or certify a valid close, missing future session, exit outcome or event mask. | `DAILY_DERIVED_OUTPUT_VERIFIED`; no formal admission |
| Preserve the research gate | No Encoder, return label, financial fit, prediction score or backtest is part of this preparation. | Candidate null/false fields, public policy and run boundaries | Formal M5 still requires a separate complete protocol decision and source-supported admission. | `NOT_RUN` |

## Candidate screen: actual saved counts

The edge counts use **CIK observation edges on retained issue intervals**, not raw supplier rows or independent companies. The prior receipt's 89,987 CIK-CUSIP rows were **not reparsed** by this screen. Among 47,125 saved exact-CUSIP8 candidate edges, 16,685 passed the accepted quality-and-CIK screen, 30,440 failed on quality, and zero fell into the unknown-quality or unusable-CIK buckets in this bounded input. The original code buckets were code 0: 17,613; code 1: 12,827; code 2: 3; code 3: 16,682; missing: 0; unknown: 0. These code counts are confined to the pinned saved candidate edges. They do not establish the provider's full code distribution or the old export's server WHERE predicate.

The interval counts use **starting-security history intervals**. Of 29,404 intervals, 13,869 had at least one screened CIK candidate and 15,535 had none; 7,124 had no CIK observation edge at all. Among the intervals, 1,573 had parallel screened edges for the same CIK, and 1,037 had multiple distinct screened CIKs. Quality-rejected edges affected 16,803 intervals; source issue codes included 6,840 incidences of `DUPLICATE_EXACT_LINK_ROW` affecting 6,066 distinct intervals. The screen's overlapping exception categories affected 24,900 distinct intervals. These overlapping counts must not be summed as separate subjects. The pinned older ledger still retains 44,514 exception records; the new view did not rebuild or replace them.

The acquisition starting set had **7,639 distinct securities**; 6,922 later-only securities (20,432 later-only intervals) remained separate. The screen counted 3,977 distinct CIK values among passing edges, which are candidate identifiers, not admitted companies. Ordinary-share eligibility, market/MIC and primary security stayed unknown for all 29,404 retained starting intervals. `historical_cik_validity=NOT_ESTABLISHED`, `formal_m5_cohort_count=null`, `admitted_observations=null`, and accepted candidate count is zero.

## Controlled commands and verification

The selected synthetic software checks ran with the pinned Python 3.11 interpreter and this checkout's `src` on `PYTHONPATH`: `python -m pytest -q tests/test_prepare_m5_candidate_screen.py` returned 6 passed, and `python -m unittest tests.test_prepare_m5_daily_rows -v` returned 10 passed. These fixtures were `SYNTHETIC_TEST_ONLY` and did not test financial outcomes. No unscreened full test suite or model integration test ran.

The candidate execution used:

```text
python scripts/prepare_m5_candidate_screen.py run --source-dir artifacts/m5_identity_candidate_ledger_20261113_v1 --output-dir artifacts/m5_preparation_candidate_20261113_v1
python scripts/prepare_m5_candidate_screen.py verify-output --output-dir artifacts/m5_preparation_candidate_20261113_v1
```

The two commands exited 0. Their externally measured process wall times were 43.212 s and 19.333 s; the corresponding safe status-log SHA-256 values were `09816db7cae1a75884078bfdd51a232f3806b3ac5be5c8141f1df35fe23b7f13` and `da2e37733119d7b51fd5a489542da597db1b03ce2d0193ac9f220248d7850db7`. Independent `verify-output` rehashed the bound inputs and saved files, reconstructed the view from the pinned old ledger and recomputed all safe counts. Peak memory was not measured (`UNKNOWN`). The private registry SHA-256 is `6293d92721bf67a31f0b56a704ef27dbc894322d68a9f4a781406ec1287ddb86`; the private output manifest SHA-256 is `779474aa184b4cfcece779b7ce51acb759846a408449d9ef0e311f1932a9a4d9`. Their contents remain local and are not in the shareable package.

## Daily row derivation: actual saved counts

The eight fixed acquisition segments contain **10,312,828 raw logical rows** from the bound prior receipts. The new row map and separate `verify-output` established **10,310,791 distinct security-date key groups**. Exactly **1,995 groups** contain 4,032 raw rows whose **all parsed source fields** agree within each key; this creates 2,037 extra raw rows relative to one representative per equal group. The remaining 10,308,796 rows are singletons. In these eight bound inputs, conflicting key groups and groups with unresolved equality were both zero. The derived candidate-row count is 10,310,791, one per nonquarantined key. This is a research-preparation row representation, not an admitted M5 event or label.

| Saved acquisition window | Raw logical rows | Security-date key groups | All-field-equal duplicate groups | Extra rows | Conflict / unresolved groups |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2019-11-01 to 2019-12-31 | 311,489 | 311,248 | 240 | 241 | 0 / 0 |
| 2020 | 1,871,118 | 1,870,783 | 325 | 335 | 0 / 0 |
| 2021 | 1,765,386 | 1,764,976 | 404 | 410 | 0 / 0 |
| 2022 | 1,671,746 | 1,671,438 | 301 | 308 | 0 / 0 |
| 2023 | 1,573,905 | 1,573,637 | 260 | 268 | 0 / 0 |
| 2024 | 1,494,518 | 1,494,291 | 221 | 227 | 0 / 0 |
| 2025 | 1,411,395 | 1,411,166 | 225 | 229 | 0 / 0 |
| 2026-01-01 to 2026-02-28 | 213,271 | 213,252 | 19 | 19 | 0 / 0 |
| **Total** | **10,312,828** | **10,310,791** | **1,995** | **2,037** | **0 / 0** |

The sparse observed-field check is at **nonquarantined security-date key** level. It records 112,076 keys with a blank `DlyClose` field and 33,469 with a blank `DlyPrc` field. It finds 10,310,755 keys with at least one blank or recognized null-literal field, largely because `DlyRetMissFlg` carries a recognized null literal on 10,277,031 keys. This is a **field-presence** diagnostic; it is neither the valid-close missing rate nor a future-session or financial-label count. The complete field-by-field raw-row and key-level counts are in the unchanged safe machine summary included in the handoff ZIP. `unknown_exit_status=NOT_CERTIFIED`, `historical_attribute_status=NOT_CERTIFIED`, and absent security sessions, accepted price/adjustment rule, final event mask and admitted observations remain `null`.

The Daily run used the existing eight-segment inventory SHA-256 `13cbec98b9ec2d6ef59a6b8f42b89a872b6789388861d114fe8413bbef6a64cd`. It bound the accepted policy and separate private decision receipt hashes above, the unchanged round-8 candidate, eight source manifests/summaries, the 2019 CSV and seven full-field saved key indexes/exception files. The normalizer code SHA-256 was `97ded6befe05d0759d1c289618a31d1cf4eab6f965d34fb01768fef1b23cae1c`; the Daily execution code SHA-256 was `b501f78497140ea3fb9298bc42bc2afcc7e6df8ba6c615877fcf950e1504ded9`. The saved SQLite logical-map SHA-256 was `76a22929cfb372f8a4bc8826304d415fac9c0c2ea9d86357cfa92aa86d686269`. Its private output manifest SHA-256 is `e3ee26f1169c455a30927eb4d74d72152437bfc1f69b98c1590e75dd2ec93c89`. The separately saved **shareable execution summary** has SHA-256 `63edf6e6bc341464c97e560eb1a270c06ee0dd03b29c56288d1678b5c4f39c28` and is included byte-for-byte in the handoff ZIP.

```text
python scripts/prepare_m5_daily_rows.py run --output artifacts/m5_preparation_daily_20261113_v1
python scripts/prepare_m5_daily_rows.py verify-output --output artifacts/m5_preparation_daily_20261113_v1
```

Both commands exited 0. Externally measured process wall times were 877.392 s for `run` and 245.304 s for the independent `verify-output`; their safe status-log SHA-256 values were `c2871aa6cffbc10b9560d404c04afc6d37b08f668b5436559491773abb8a5be8` and `579043f60f4f099a31232ce3676ca49293e5cfa4aaa23e68a7d75fde9d016e29`. The saved summary's **866.242 s** internal timer starts after the run's source preflight and excludes the separate verification. Peak process memory was not measured (`UNKNOWN`). The verifier read the saved SQLite mapping and sparse field-presence table; it did not compute returns, move missing horizon endpoints or certify the applicable exchange/session grid.

## Remaining evidence and decisions

The identity screen is a review queue. `cikdate1`/`cikdate2` are filing-observation dates rather than proven CIK valid-from/to. Historical issuer identity, class eligibility, applicable event-time MIC and a documented primary issue remain unresolved. The accepted Daily derivation rule does not settle the backend duplicate cause, valid close semantics, an exit/terminal outcome or a text-and-price availability clock. A complete versioned M5 protocol, formal event mask, admitted observations and financial execution remain pending. The [decision sheet](../docs/M5_IDENTITY_NEXT_DECISIONS_20261113.md) lists the remaining full-protocol choices without reopening the three accepted preparation rules.
