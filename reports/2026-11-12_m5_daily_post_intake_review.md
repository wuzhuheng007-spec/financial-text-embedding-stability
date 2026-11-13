# M5 daily post-intake engineering review

**Scope:** saved-output verification, duplicate-index diagnosis, and the next
manual acquisition plan. M5 remains PROPOSED, researcher_accepted=false,
NOT_RUN. This review did not construct an Encoder, compute return labels, train
or score a financial model, backtest, or submit a WRDS request.

## Plan, actual, evidence, limit, status

| Plan | Actual | Evidence | Limit | Status |
| --- | --- | --- | --- | --- |
| Reconcile the eight saved daily receipts | Existing `review_daily_receipts.py verify-output` returned 0. Eight saved identities remain consistent. | [Eight-segment safe report](../artifacts/m5_daily_eight_segments_receipt_review_20261112_v1/m5_daily_eight_segments_report_safe.md), inventory SHA-256 `13cbec98b9ec2d6ef59a6b8f42b89a872b6789388861d114fe8413bbef6a64cd`; tool SHA-256 `ff0cd34238ec366c51cd7519682b9c54b4e427ecab5251155973038f08aea29c`. | The verifier compares saved summaries, manifests, code and addenda; it does not rescan raw CSVs or certify vendor completeness. | PASS_SAVED_BINDINGS_ONLY |
| Resolve the 2019 execution-record gap | No old standalone execution ledger was located. The original verifier was run now in read-only `verify-output` mode: exit 0; five old saved files were unchanged. | New ignored execution ledger SHA-256 `613ac3d004327f54b58f603a4bf2f6d11a2620eeb7cb6e75ca79c9aee42fd66f`; original tool SHA-256 `8e8e80f4cd76a4892195dfdd603038b4ae1c6e6c6fcff87f07ebaaafbd00ce76`. | This is a current-date verification, not proof of the earlier run's undocumented exit status. The saved result remains `CSV_SCOPE_OR_KEY_REVIEW_REQUIRED`. | CURRENT_SAVED_OUTPUT_VERIFIED; OLD_LEDGER_UNKNOWN |
| Diagnose repeated daily keys | Eight manifest-bound exception indexes reproduce 1,995 `PERMNO` + `DlyCalDt` duplicate groups and 2,037 extra rows. Multiplicity: 1,954 two-row, 40 three-row, and one four-row group; saved parsed-field conflicts: 0. | New ignored duplicate review SHA-256 `223f2119bcfc43606b85ff474bce571b788027b32149fce782099588417194d5`, built from the eight exception indexes and the eight-segment inventory. | No original CSV was rescanned. Parsed-cell equality, a parser-consumed interval hash, and strict isolated-record byte identity have different scopes. Backend SQL/JOIN and causation remain unknown. | DUPLICATES_CONFIRMED_IN_SAVED_INDEXES; ROOT_CAUSE_UNKNOWN |
| Prepare a discriminating follow-up | A private, unsubmitted plan identifies one two-row anomalous key and one same-date control from the bound starting-issue set. | Ignored `manual_duplicate_probe_private.json` and `manual_duplicate_probe_steps_private.md`; the selected index is hash-bound. | The authenticated form may not permit one-day/variable-controlled queries or disabling implicit joins. No query was submitted. | MANUAL_PROBE_PROPOSED |
| Prepare distributions and corporate actions | Created a conditional [manual download plan](../docs/M5_WRDS_DISTRIBUTIONS_NEXT_DOWNLOAD.md) and marked the older daily-file page as historical. | [CRSP CIZ guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf) and [WRDS CRSP product catalog](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/center-for-research-in-security-prices-crsp/), checked 2026-11-12. | The logged-in distribution form, dictionary mapping, automatic joins, release and export rights were not verified. | CONDITIONAL_DOWNLOAD_PLAN_READY |

The 2019 page and DAT supplements remain bound to the first-segment receipt.
The 2020 Query ID is user-provided without an independent saved page receipt;
the 2021 Query ID remains UNKNOWN. Server Input DAT for other years, native
backend SQL/JOIN, exact product release, project export terms and provider
universe completeness are separate unresolved gates. A result-page or
parameter match cannot substitute for them.

## What the duplicate evidence says

The saved eight-segment inventory reports 10,312,828 rows including repeats;
its date sets do not overlap. The new index review confirms the duplicate
totals without reading the old CSVs again. The 2020–2026 summaries count 1,796
extra rows with identical parsed cells; 2019 has no parsed-repeat measure.
The 2022–2026 summaries count 1,051 repeated parser-consumed interval digests,
which can incorporate blank physical lines and do not establish isolated CSV
record byte identity. These figures have different year denominators and must
not be merged into an eight-year exact-byte claim.

The original auditors use strict CSV parsing and preserve every reported
duplicate. No parsed-field conflict was reported in the saved summaries.
Possible server-side row expansion, same-date distribution events, and parser
boundary effects remain hypotheses. The public WRDS `CRSPMERGE` SIZ macro and
the CIZ-to-SIZ conversion example are not the backend SQL of this daily form;
their `DISTINCT` logic is not applied to the saved export. The private manual
probe is the next smallest query design if the form supports it. A response
with repeated records would narrow the local-parser explanation but would not
identify the backend join; a response without repeats would require checking
the query version and settings.

## Distribution acquisition gate

The next candidate source role is CIZ `StkDistributions`, with the already
bound 7,639 starting `PERMNO` identifiers and a proposed `DisExDt` buffer of
2019-11-01 through 2026-02-28. First inspect the current authenticated form
and variable dictionary. If the intended PERMNO upload, `DisExDt` filter and
field mapping are confirmed, request only the short 2019-11-01 to 2019-12-31
batch first and preserve all event types and sequence numbers. A declaration,
record or payment date may lie outside an ex-date window; retain the complete
event and flag that boundary. The full field list, source evidence and saved
artifacts appear in the linked plan. No daily-price or return policy changes
are implied by receiving distributions.

## Execution and integrity

The checkout was `codex/research-plan-round10-20261106` at
`84113de61488ffc342e75491c1fd72dd3691bba1` with no tracked changes
before this review. Python 3.11 imported `finembed` from this checkout's
`src/finembed/__init__.py` with explicit `PYTHONPATH=src`. The round-8 M5
candidate SHA-256 remains
`f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38`.

Actual focused commands and observed exits:

| Command | Exit |
| --- | ---: |
| `python artifacts/m5_daily_eight_segments_receipt_review_tool_20261112/review_daily_receipts.py verify-output --output-dir artifacts/m5_daily_eight_segments_receipt_review_20261112_v1` | 0 |
| `python artifacts/m5_daily_first_segment_tool_20261111/audit_daily_segment_local.py verify-output --output-dir artifacts/m5_daily_first_segment_20191101_20191231_v1` | 0 |
| `python artifacts/m5_daily_post_intake_review_tool_20261112/build_post_intake_review.py` | 0 |
| Targeted synthetic `exception_rows` JSON/JSONL parse and multiplicity check (`python -c`, in memory and temporary fixtures only) | 0 |

All Python commands used the user-specified Python 3.11 interpreter and this
checkout's `src` on `PYTHONPATH`. The new private builder SHA-256 is
`182da60324ff3ec051baea63ef20b669b4df1536552c2d4ca3c296898c26bdf6`;
its saved output was read back and hash-checked. The original ZIPs, CSVs,
indexes, private probe list and execution ledgers remain in Git-ignored local
`artifacts/`. No old receipt was overwritten.

**Output-control deviation:** During an exploratory private exception-index
schema check, a mistaken aggregation expression sent identifier, date, row
number and digest text to a truncated tool output. The command exited 0; the
number of identifiers visible in the conversation cannot be reliably counted.
It did not read original price CSVs or output price/return fields, and wrote
nothing to files or Git. That path was stopped and subsequent diagnostics used
fixed aggregate fields only. The claim that no private identifiers entered the
model context is therefore not supported for this review.

The pending research choices remain historical-as-of versus fixed-snapshot
interpretation, eligible markets and primary issue, CIK historical validity,
text and close availability clocks, exit and return policy, and formal cohort
definition. No saved engineering receipt admits a formal M5 observation or
freezes these choices.
