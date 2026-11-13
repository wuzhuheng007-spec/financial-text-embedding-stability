# 2020 distribution-only group: targeted saved-index review

**Scope:** ENGINEERING_ONLY. This review examines the one 2020 distribution
multi-sequence group outside the saved daily **duplicate-key** set. It uses
saved, hash-bound indexes; it neither rescans the historical CSV audits nor
admits a formal M5 observation. M5 remains PROPOSED,
researcher_accepted=false, and NOT_RUN.

## Plan, actual, evidence, limits, status

| Plan | Actual | Evidence | Limit | Status |
| --- | --- | --- | --- | --- |
| Freeze the prior comparison and sources | The handoff, prior safe result ZIP, merged distribution output, 2020 daily receipt, full key index, and unchanged M5 candidate were bound by SHA-256. | Saved `single_group_summary_safe.json` SHA-256 `016edde7df763dcae36f09d111ba755a35843a6984dd1268f3b93b190f0d527b`; full source hashes are in that file. | File identity does not establish the exact provider release, license scope, or backend query logic. | BOUND_SAVED_INDEXES_VERIFIED |
| Select the sole discrepancy | Exactly one saved 2020 group has state `DISTRIBUTION_MULTI_SEQUENCE_ONLY_RELATIVE_TO_DAILY_DUPLICATE_SET`. It contains two saved distribution rows with two distinct sequence numbers. | The bound merged private comparison and event index were queried locally; the safe summary retains only counts. | Absence from a duplicate-key set does not imply absence from all daily records. | ONE_TARGET_GROUP_CONFIRMED |
| Check the complete saved daily key index | The target security-date key has **zero rows in the bound saved 2020 daily extract**. The security occurs on another saved daily date, and the target date occurs for other securities. | `daily_target_key_row_count=0`, `target_security_seen_on_other_daily_date=true`, and `target_date_in_daily_saved_set=true` in the saved safe summary; daily full-index SHA-256 `bd8db7f91d25241d6f043aa31acc2e89f4879abf03d1be0a3d805d643b32663e`. | This does not establish supplier omission, a filter mechanism, a holiday, delisting, or a valid financial sample. | NO_SAVED_DAILY_ROW_FOR_TARGET_KEY |
| Compare full 2020 saved date sets | Distribution `DisExDt` has 254 distinct dates; daily `DlyCalDt` has 253; the intersection is 253, the distribution-only date count is 1, and the daily-only count is 0. The target date is **in the daily date set**, so it is not the one distribution-only date. | Safe summary date-set fields, recomputed from the bound event and full daily key indexes. | These are saved-date sets, not an independent trading-calendar ruling. The 254-versus-253 difference does not explain the target key's absence. | DATE_SET_COMPARISON_COMPLETE |
| Preserve and verify | One targeted local run and one separate `verify-output` both exited 0. Internal verification also passed. Nine targeted synthetic tests passed. | Fixed terminal statuses and the saved execution ledger; the re-read verification rederived both safe and private results. | Peak memory was not measured. Original receipts and CSVs were unchanged. | SAVED_OUTPUT_VERIFIED |
| Check remaining source gaps | A limited local search found no native distribution SQL or server Input DAT. The earlier daily backend JOIN remains unverified. | Source-gap fields in the safe summary and targeted search record. | No new WRDS query or download was performed. | SOURCE_PROVENANCE_PARTIAL |

## Interpretation and boundaries

The prior statement "distribution-only" was relative to **daily duplicate
keys**. The full saved daily key index now narrows this one case: the target
security-date pair has zero rows in the saved 2020 daily extract, despite that
date existing for other securities and that security appearing on other saved
dates. The local saved-date difference occurs at another date. These facts do
not identify why the target pair is absent, nor do they establish a WRDS
backend JOIN explanation. No event row, identifier, date pair, amount, or
private hash is disclosed here.

The source files match their saved identities: merged CSV SHA-256
`e4116783a981161f6bd2da06e9acd3f682cb0d6fdd5eb6da3cb71e56285306f1`,
daily CSV SHA-256
`f8442af8835c83db88051c2a014944fe4534448ab8edb515423e897e057e86ac`,
and targeted review code SHA-256
`b69fb2b97b7cce51e561c8b0cf203ae0ee8938fdb63be404595dab102f075278`.
The safe result also binds the two saved manifests, the private indexes, the
handoff, and M5 candidate SHA-256
`f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38`.
Provider release, license/export scope, supplier completeness, and formal M5
admission remain unresolved. The earlier private-index output-control
deviation remains part of the historical record.

## Execution record

The verified starting branch was `codex/research-plan-round10-20261106` at
`d552e3c979d38b9f280fde5ab6e295ba6aec0a53` with a clean tracked
worktree. Fixed review code commit:
`b8d640f96fd2565d742ec42661c1690bba5ddf62`. Python 3.11 was run with
this repository's `src` on `PYTHONPATH`.

| Actual command (paths sanitized) | Outcome |
| --- | --- |
| `python -m pytest -q tests/test_review_m5_distribution_2020_single_group.py` | 9 synthetic checks passed; exit 0. |
| `python scripts/review_m5_distribution_2020_single_group.py run --handoff-zip <bound handoff ZIP> --output-dir <new ignored directory>` | One attempt; `M5_DISTRIBUTION_SINGLE_GROUP_SAVED_NO_SAVED_DAILY_ROW_FOR_TARGET_KEY`; exit 0. |
| `python scripts/review_m5_distribution_2020_single_group.py verify-output --output-dir <same ignored directory>` | `M5_DISTRIBUTION_SINGLE_GROUP_SAVED_OUTPUT_VERIFIED_NO_SAVED_DAILY_ROW_FOR_TARGET_KEY`; exit 0. |

The run ledger spans 2026-11-12T09:14:28.997690+00:00 to
2026-11-12T09:14:38.012368+00:00, or 9.01 seconds including its internal
saved-output verification. The separate verify command took 5.40 seconds at
the tool boundary. These are local engineering wall times. There were zero
new WRDS queries, Encoder sessions, return labels, training runs, prediction
evaluations, and backtests. Peak memory is UNKNOWN.
