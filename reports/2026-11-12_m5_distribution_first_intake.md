# First CRSP distribution segment: local engineering intake

**Scope:** ENGINEERING_ONLY. This is a bounded 2019-11-01 to 2019-12-31
distribution-file receipt and a finite comparison with the saved 2019 daily
duplicate-key index. It is not a formal M5 cohort, a financial label, a
prediction result, or proof of a WRDS backend join. The round-8 M5 candidate
remains PROPOSED, researcher_accepted=false, NOT_RUN, with SHA-256
`f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38`.

## Plan, actual, evidence, limit, status

| Plan | Actual | Evidence | Limit | Status |
| --- | --- | --- | --- | --- |
| Verify handoff and source bytes | The 15-member handoff ZIP passed CRC, member-set, size and manifest SHA checks. The nested ZIP passed CRC and held one CSV member. The original handoff was unchanged after reading. | Handoff SHA-256 `8d905753e704baf3408be9254317546e18d610dfbb4fafc7f1098840b0442ead`; nested ZIP SHA-256 `733f344b558782fce4d46c84308f1347c6eb83cd7cf3dd62cdf377f4d0a6d09c`; saved private manifest. | Transport identity does not authenticate the provider's entire data universe. | PASS_TRANSPORT_AND_SAVED_BYTES |
| Bind the identifier upload | The package TXT and previously bound starting-issue TXT are byte-, order- and set-identical: 7,639 unique positive decimal identifiers, ASCII LF with final newline. | TXT SHA-256 `017224e43461409fa6f2ef007d182a5b0d870f02cd1edc4c528f0a901a543318`; saved upload comparison. | The package TXT is not a server Input Files DAT for Query 11900944. No server attachment was bound. | PASS_LOCAL_LIST_BINDING; SERVER_ATTACHMENT_PENDING |
| Compare page, parameters and SQL | The result screenshot shows Query 11900944, `crsp_q_stock`, Success, 5,670 rows, a rounded 73 KiB, and a Download SQL button. The user-pasted parameters specify `stkdistributions`, a Header-based PERMNO query variable, `DisExDt` dates and ten explicit `DisType` codes. No SQL was in the package or located by Query ID/result stem in registered local positions. | Saved result screenshot and user parameter text, separately hash-bound in the private receipt. | Pasted parameters are not native complete SQL. Earlier form images are not proof of final upload or condition state. Backend JOIN, full predicates, release and export terms remain unverified. | QUERY_METADATA_BOUNDED; SQL_NOT_LOCATED |
| Audit the actual CSV | The extracted 666,470-byte CSV has 5,670 logical data rows, 22 columns, all 22 selected names, no extra or duplicate column names, no bad-width row, and no recorded structural issue. It spans 40 observed `DisExDt` dates from 2019-11-01 to 2019-12-31. | CSV SHA-256 `2d1232fca547d9aacb121842bf10cc2c69b5513b75ef02ff27628ca02d90b138`; saved summary SHA-256 `17f5512d34588d525ac6e4b686f62f3545bf728ba67572b9f3f65845ba757685`. | The date window applies only to `DisExDt`; other event dates may fall outside it. Page size is rounded and is not an exact byte-count test. | PASS_STRUCTURE_ONLY |
| Review event identity and missingness | 4,169 starting identifiers have at least one row; 3,470 have none in this bounded extract. There are 240 `PERMNO` + `DisExDt` groups with multiple sequence numbers, and no repeated candidate `PERMNO` + `DisExDt` + normalized `DisSeqNbr` key or uncertain sequence. | Saved summary and private event index. | An unobserved identifier is not a failed download or a formal exclusion. A candidate key unique in this extract is not a certification of the provider's complete key or event coverage. | CANDIDATE_KEYS_UNIQUE_IN_SAVED_EXTRACT |
| Compare with saved daily duplicates | All 240 saved 2019 daily duplicate-key groups have a same-identifier, same-date distribution group; the 240 multi-sequence distribution groups have exactly the same key set. For every group, the distribution row count and distinct candidate-sequence count equal the saved daily group multiplicity. An independent read of the two hash-bound private indexes reproduced the key-set and multiplicity checks. | Distribution event-index SHA-256 `c5335eb2a0c06f01efd8b6ca2b8ca85c34437e437e6caf5838328418f8e3f493`; daily exception-index SHA-256 `d6822b09372a1d1d9b3f3c6d47b5e3a5cb3f2b84df9ebf53e2ac1a7ebc4dc71a`; daily summary SHA-256 `561e98eae9b33b71650e6aa62501532b03d4753d29736bef38aeeb440cb1d9ec`. | This is a strong structural association in this saved first segment, not proof that a distribution JOIN generated daily repeats. The native SQL is missing, and the comparison has no independent nonduplicate daily-key denominator. It does not generalize to the other seven segments. | ASSOCIATION_CONFIRMED; CAUSE_UNKNOWN |
| Verify saved output | `run` performed one internal verification, then a separate `verify-output` re-read the saved CSV, package, source binding, private event index and daily comparison; both exited 0. | Code commit `d53f79cf55a4983d63234979edd3e159393349d0`; auditor SHA-256 `ab6f37d07289f960232df8be7392acf450121e3ae66ad6c95d7230a0e6f3dec0`; private manifest SHA-256 `402f71aae7370af5202960d6a8e5ad6016456114890c66ff1efef94292d9c648`. | Recalculation uses the fixed audit implementation; the independent key-set check provides a narrower second implementation of the notable association. | SAVED_OUTPUT_VERIFIED |

The 5,670 rows contain seven observed `DisType` codes; the ten codes in the
user-pasted predicate remain an **explicit filter**, even if the official
category list includes those codes. `N/A` is a literal distribution type in
the [CRSP flag table](https://www.crsp.org/wp-content/uploads/appendix/FlagType_AT.html),
not a missing-value marker. Forty `DisDivAmt` cells are blank. Nondecimal or
special original strings appear in 5,667 `DisPERMNO` and 5,665 `DisPERMCO`
cells; they were preserved, not coerced to missing identifiers or successors.
The meanings of these values and amount units still require the authenticated
dictionary. No event amount was aggregated or turned into a return.

Other event dates crossed the ex-date query window in 1,126 declaration-date,
53 record-date and 1,114 payment-date cells. These counts can overlap rows
and must not be added as unique events. No row was discarded for these dates.
The selected columns `SICCD`, `PrimaryExch` and `NASDIssuno` are documented in
CRSP security information Header/History, while `DisSeqNbr` and distribution
fields are in `StkDistributions`; `PERMNO` occurs in both roles. This corrects
the package's provisional source split. Without native SQL or expanded field
metadata, actual SELECT provenance and JOIN behavior remain UNKNOWN.
[CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf)
documents the source roles. The auditor preserved full original rows and the
private event index; it did not deduplicate or fill values.

## Commands, measurement and next acquisition

The checkout began at `1d8d44ef615c589a92978864b75ea1ad9d501ad8` on
`codex/research-plan-round10-20261106`, with a clean tracked worktree. Python
3.11 used this checkout's `src` on `PYTHONPATH`; `finembed` imported from the
current repository. The fixed auditor code was committed before real intake.

| Command | Observed result |
| --- | --- |
| `python -m pytest -q tests/test_audit_m5_distribution_segment.py` | 7 synthetic tests passed; exit 0. Earlier synthetic development runs had failures that were corrected before intake. |
| `python -m py_compile scripts/audit_m5_distribution_segment.py` | Exit 0. |
| `python scripts/audit_m5_distribution_segment.py run --handoff-zip <local handoff ZIP> --output-dir <new ignored directory>` | `M5_DISTRIBUTION_SEGMENT_SAVED_PASS_STRUCTURE_ONLY`; exit 0. |
| `python scripts/audit_m5_distribution_segment.py verify-output --output-dir <same ignored directory>` | `M5_DISTRIBUTION_SAVED_OUTPUT_VERIFIED_PASS_STRUCTURE_ONLY`; exit 0. |
| Independent hash-bound index key-set and multiplicity check | 240/240 key-set and multiplier agreement; corrected check exit 0. One initial ad hoc assertion check exited 1 without changing data. |

The run ledger measures 1.402 seconds from its UTC start to completion,
including extraction, analysis and internal saved verification. The separate
verify command exited 0; process peak memory was not measured and remains
UNKNOWN. These timings are local CPU data-engineering operations, not model
inference. Full commands and original paths are retained only in private local
evidence. Original ZIP, CSV, screenshots, uploaded list, event rows, daily
comparison rows and private ledgers remain Git-ignored.

The next **proposed manual** distribution batch is `DisExDt` 2020-01-01 to
2020-12-31, with the same bound starting-issue TXT, the same 22 selected
columns and the same explicit ten-code `DisType` predicate if the authenticated
form confirms each setting. First preserve/download the SQL for Query 11900944
from the visible result-page button if still available, and inspect its
SELECT, source table, JOIN and predicates. If the form or SQL shows different
semantics, document the difference before repeating the query. No new WRDS
query was submitted by this intake.

This receipt does not settle product release, project export permission,
provider universe completeness, historical CIK links, security eligibility,
event-to-return or exit policy, financial sample admission, or the M5 research
protocol. No Encoder session, embedding, return label, financial training,
prediction evaluation or backtest was run.
