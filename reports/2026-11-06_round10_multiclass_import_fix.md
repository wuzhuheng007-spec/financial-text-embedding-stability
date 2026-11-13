# Round 10: multiclass CIK import-validation correction

**Status:** `SYNTHETIC_STRUCTURE_REPRODUCED / NARROW_VALIDATOR_FIX_VERIFIED / REAL_IMPORTS=0 / M5_PROPOSED`. Work began from clean commit `26aea807b1834f41acc035195cfe3d2094e75941` on `codex/research-plan-round10-20261106`. The [round-8 M5 candidate](../configs/m5_financial_protocol_round8.json) retains SHA-256 `f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38` and remains `PROPOSED`, `researcher_accepted=false`, `NOT_RUN`. The [round-9 calendar](../configs/nyse_calendar_rules_round9.json) and saved coverage receipt were read for identity only; no calendar or price result was recomputed.

## Plan, actual execution, evidence, deviation and status

| Planned item | Actual execution | Evidence | Deviation or boundary | Status |
| --- | --- | --- | --- | --- |
| Reproduce the reported multiple-class false rejection | Built one explicitly `SYNTHETIC_TEST_ONLY` canonical bundle from the existing test helper and added two distinct nonprimary securities alongside one declared primary with a nonempty basis for the same CIK and effective interval | Ignored `artifacts/round10/synthetic_multiclass_input_before.json`, SHA-256 `14e97f045bd13c53f4eaa55952f2fafc28ed3da1513f238e7522c2b9a0da97ec`; original validator SHA-256 `2b771961ae7a03948eff369772407117995c724b9a64cc88a24a9e6d67cd640c` | The old pairwise test compared the two nonprimary rows and emitted `AMBIGUOUS_CONCURRENT_SECURITIES` despite the concurrent primary | REPRODUCED |
| Correct the structural rule | Updated [validator](../scripts/validate_m5_import_round9.py) to inspect each half-open effective-date segment for each CIK as a group; one declared primary with a nonempty basis and additional nonprimary classes passes structure, zero primaries reports `NO_PRIMARY_SECURITY`, and multiple primaries report `MULTIPLE_PRIMARY_SECURITIES`. Duplicate overlapping CIK/security links remain rejected | Source diff and current validator SHA-256 `bae792fa4d9465af218a010ff6d7284b3293806757e639f84166cdd02147547c` | A nonempty `primary_basis` is only a structural claim; the validator cannot authenticate its date, source or applicability | IMPLEMENTED |
| Verify the original saved input with the repaired code | Reused the exact input bytes and wrote a new receipt, preserving the original failed receipt | Before: SHA-256 `419bac4521c96678d21450699ced461df157826f5af11552cd23deb1342245b4`, `REJECTED_STRUCTURE_ONLY`, sole issue `AMBIGUOUS_CONCURRENT_SECURITIES`; after: SHA-256 `b4190f4cf25d39b8f44cbb9b835ec086e69916b74b52719d545b0446272fe338`, `PASS_STRUCTURE_ONLY`, zero issues | The old CLI attempt was nonzero; the PowerShell wrapper reported exit code 1, which is not asserted as the exact Python process status | VERIFIED, SYNTHETIC ONLY |
| Test boundary and failure cases | Focused tests passed 18/18 in 1.80 seconds; checked all permutations of three class rows, no primary, two primaries, adjacent primary handoff, open-ended intervals, duplicate same-issue link, missing basis, invalid flag, unknown source and retained earlier import checks | [Focused tests](../tests/test_validate_m5_import_round9.py); ignored `artifacts/round10/targeted_tests_v2.log`, SHA-256 `4c4703a8ffb00a9a2c31eb312fc99a10502586c8b3aea25a7dccd32c9ff241ac` | All rows and the successful CLI receipt are synthetic; no production vendor mapping or financial row is certified | PASS |
| Preserve prior research identities and financial boundary | Rechecked unchanged M5 candidate, round-9 saved calendar manifest and XNYS coverage receipt; a read-only audit of project documents, configs, registered round-7–10 artifacts and referenced historical M1/M4 identities found no newly authorized product file and zero real canonical imports | Calendar manifest SHA-256 `ebd95c7f293088545706a0fac65c8c25427748a0e1f01ded88b3ee950c0d3565`; coverage SHA-256 `69664fe4b82efe8281b3d0c7e041ac29c46c3c347cc4cd7702e5e202235acc86`; local synthetic source-binding summary SHA-256 `c98a66ee06d8bcaca1dbcec63e6c0d36c2c962fa3c1538d2a3f8127f20293b6c` | This bounded audit is not a global filesystem or account-entitlement search; structural success does not create an admitted M5 observation | UNCHANGED / NOT RUN |

The earlier validator's pairwise rule detected two concurrent nonprimary rows and rejected them independently of a third declared primary with a nonempty basis. The repair retains an explicit interval ledger: for every segment with active links, it records the CIK, interval and active security IDs before evaluating the number of primary claims. This removes row-order dependence for the three-class case. It preserves fail-closed handling for a single nonprimary link, a later segment without a primary, competing primary claims, and duplicate overlapping links for the same issue. It does not select a security by price availability, ticker or later outcome.

## Commands and identity checks

Commands ran in the round-10 checkout with the verified Python 3.11 interpreter and `PYTHONPATH` explicitly set to this checkout's `src`. The private interpreter path is omitted from this shareable report. The synthetic input was generated locally from `tests/test_validate_m5_import_round9.py::synthetic_bundle`; its saved SHA-256 above, rather than an invented command, identifies the exact fixture bytes.

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
python scripts/validate_m5_import_round9.py --input artifacts/round10/synthetic_multiclass_input_before.json --output artifacts/round10/synthetic_multiclass_receipt_before.json
# nonzero: REJECTED_STRUCTURE_ONLY issues=1; original receipt preserved
python -m pytest -q tests/test_validate_m5_import_round9.py
# exit 0: 18 passed in 1.80s
python scripts/validate_m5_import_round9.py --input artifacts/round10/synthetic_multiclass_input_before.json --output artifacts/round10/synthetic_multiclass_receipt_after.json
# exit 0: PASS_STRUCTURE_ONLY issues=0; new receipt, same input hash
python -m py_compile scripts/validate_m5_import_round9.py tests/test_validate_m5_import_round9.py
# exit 0 using the verified Python 3.11 executable
git diff --check
# exit 0; line-ending advice was informational
```

The saved successful receipt binds the **same** synthetic input SHA-256 `14e97f045bd13c53f4eaa55952f2fafc28ed3da1513f238e7522c2b9a0da97ec` to the repaired validator SHA-256 `bae792fa4d9465af218a010ff6d7284b3293806757e639f84166cdd02147547c`. The local `artifacts/round10/synthetic_repro_summary_v2.json` (SHA-256 `a158bc8536f0581833aafe24f34b063e8ffd4da1b50082ab57ad5e6d01158830`) binds both receipt hashes, code versions, the input hash and final test-log hash; v1 remains preserved. The successful receipt has `admitted_financial_observations=null`, `returns_or_scores_computed=false`, and `provenance_or_license_verified=false`. The earlier failed receipt binds the previous code hash and is retained rather than rewritten.

## Remaining real-import gates

`PASS_STRUCTURE_ONLY` does not establish an as-of CIK-to-security join, source licensing, real dated evidence for `primary_basis`, or truth of `ambiguity_flag`. The independent review also identified broader, preexisting checks still needed before a real import: cross-table CIK/text-link completeness; containment of a link's effective interval within the corresponding security-master interval; detection of the same issue ID assigned to overlapping different CIKs; and a frozen meaning for `ambiguity_flag`. These were not expanded into this narrow fix. Source product/version and raw-file identity, historical listing market, text-publication clock, actions/exits and the M5 researcher decision remain separate gates. No real authorized vendor file was imported, and no financial observation was admitted.

This round created no Encoder session, model vector, financial label, fitted predictor, prediction score, backtest or economic conclusion. The round-9 calendar/coverage receipts and S2/S3 engineering results remain historical evidence with their original scopes; this software correction does not extend them or freeze M5.
