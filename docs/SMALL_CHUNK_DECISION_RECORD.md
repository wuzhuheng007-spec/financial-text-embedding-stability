# Version-bound small-chunk engineering decision record

The [candidate](../configs/small_chunk_probe_round2.json) file remains
`PROPOSED`, `researcher_accepted=false`, and `real_inference_status=NOT_RUN`;
any later researcher decision is a separate, version-bound record. The
[template](../configs/small_chunk_decision_template.json) requests that
decision. Its `decision=NOT_PROVIDED` and other placeholders cannot authorize
model loading. This decision concerns one
five-company engineering probe, not an M5 protocol freeze, a formal financial
study, or acceptance of the colleague's reported result.

## Decision scope and required record

The only execution scope recognized by this validator is
`small_chunk_engineering_probe_real_inference_five_company_full_grid`. A
researcher can write `ACCEPT` or `REJECT` in a separate JSON record; a missing
or deferred decision remains `NOT_PROVIDED`. The record must identify an actual
written decision source as `source_reference={"path": ..., "sha256": ...}`.
For new records, use `original_message_at_utc` for the actual source-message
time if known, or JSON `null` if unknown. Use `recorded_at_utc` for the actual
time the decision record was created. A null original-message time must not be
replaced with the record-creation time. Both known times must be ISO 8601 UTC
timestamps ending in `Z`, and the original message cannot be later than record
creation. Legacy records with only `decided_at_utc` remain valid; do not mix
the legacy and new fields in one record. The source path
may be absolute or relative to this checkout, but must resolve inside its
ignored, untracked `artifacts/` directory. The file must exist, contain
non-whitespace bytes and match its SHA-256. A committed source is rejected.
The validator cannot authenticate a person, signature, or the meaning of the
source by hashing; an independent human must confirm that the source really
conveys the researcher's decision before any execution.
The actual decision record must also reside inside this checkout's ignored,
untracked `artifacts/` directory. The committed template cannot authorize
inference by changing a single field.

An `ACCEPT` record must exactly match all current bindings:

| Field | Meaning and source |
| --- | --- |
| `candidate_sha256` | SHA-256 of the unchanged candidate JSON bytes. |
| `input_versions` | Ordered list of five records, each with `ticker`, ten-digit `cik`, `accession`, `form=10-K`, `section=ITEM_1_BUSINESS`, and exact `item1_sha256`. Obtain these from the current validated M1 corpus/preflight, not from the template. |
| `input_manifest_sha256` | `canonical_json_hash({"corpus_index_sha256": candidate.input.corpus_index_sha256, "documents": input_versions})`. This binds the corpus index and ordered section versions. |
| `model_identity` | Exact pinned model ID, model/tokenizer revisions, hashes for `onnx/model.onnx`, `tokenizer.json`, `config.json`, `sentence_bert_config.json`, CPU provider, and reference smoke-config SHA-256. Source these from the current checked model/preflight. |
| `metric_contract_sha256` | SHA-256 of the current metric-contract file bytes. A changed contract requires another decision. |
| `company_failure_policy` | The full five-company order; missing/failed company means `FAIL_ARM_NO_SUBSET`, zero/nonfinite vector means `FAIL_ARM_WITH_REASON`, undefined metric means `NULL_WITH_REASON`. |

Canonical JSON uses UTF-8, sorted object keys, compact `,`/`:` separators,
`ensure_ascii=False`, and rejects NaN. The order of `input_versions` remains
significant. A hash records bytes and version identity; it does not certify
scientific merit, authorship, or the authenticity of the human decision.

## Validation status and use

`finembed.small_chunk_decision.validate_decision(record_path, *,
candidate_path, input_manifest_sha256, input_versions, model_identity,
metric_contract_path)` returns a status and a reason:

| Status | Meaning | Model loading |
| --- | --- | --- |
| `MISSING` | No record, no explicit choice, absent/empty/nonprivate source, or missing/invalid required time fields. | Forbidden |
| `REJECTED` | Explicit rejection with source and valid UTC time fields for the declared scope. | Forbidden |
| `WRONG_SCOPE` | The record does not name this exact full-grid probe. | Forbidden |
| `VERSION_MISMATCH` | Source SHA changed, accepted record differs from current candidate, input, model, metric, or failure-policy identity, or the caller's expected identity is invalid. | Forbidden |
| `ACCEPTED` | Explicit acceptance and all required version bindings match syntactically. | Only eligible after independent decision-source review and fresh preflight verification |

`canonical_json_hash` and `derive_input_manifest_sha256` are exported to build
the expected input identity. The caller must construct `input_versions` and
`model_identity` from independently checked preflight data, verify the bound
source/model/code files, and reject any stale receipt before model loading.
The decision record is not a substitute for that preflight. Do not obtain the
expected values by copying the record being validated. Do not change the
candidate's `PROPOSED` status or reuse its JSON as an inference config.

Keep an actual signed or written decision source and its record under the
project's appropriate private evidence controls. The software requires the
source to be ignored and untracked; it does not read or publish its content.
Do not commit private review material merely to make this software check pass.
A synthetically populated record used in tests verifies only validator behavior
and is not researcher acceptance. The returned `record_binding_match=true`
means only that a record's fields and local source bytes match; it is not a
claim that human identity or authorization was independently verified.
