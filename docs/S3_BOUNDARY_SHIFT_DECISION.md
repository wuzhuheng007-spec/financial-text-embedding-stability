# S3 boundary-shift decision record

**Scope:** the fixed five-company, one-model, CPU engineering S3 grid in
[`boundary_shift_probe_round5.json`](../configs/boundary_shift_probe_round5.json).
The candidate retains its original `PROPOSED`, `researcher_accepted=false` and
`real_inference_status=NOT_RUN` bytes. The later human decision belongs in a
separate ignored, untracked local `artifacts/` JSON record. The source message
belongs in a separate ignored, untracked source file. Do not place either in
Git, a transfer package or a synthetic test fixture used as research evidence.

The validator is `finembed.boundary_shift_decision.validate_boundary_decision`.
It returns distinct `ACCEPTED`, `REJECTED`, `MISSING`, `WRONG_SCOPE` and
`VERSION_MISMATCH` statuses. Only `ACCEPTED` permits the runner to continue to
its other pre-Encoder gates. A hash match checks record bytes and version
bindings; it cannot prove who authored a message or authenticated the named
researcher. An agent's scope interpretation is not a verbatim user statement.

## Required private record fields

| Field | Required value or meaning |
| --- | --- |
| `schema_version`, `kind` | `1`, `boundary_shift_decision_record` |
| `decision`, `decision_scope` | Explicit `ACCEPT` or `REJECT`; exact S3 scope `s3_boundary_shift_engineering_probe_real_inference_five_company_full_grid` |
| `researcher_name` | Exact name supplied for this S3 scope (`zhuheng wu`); software does not authenticate identity |
| `source_reference` | Relative or absolute path to the ignored, untracked, nonempty source file and that file's SHA-256 |
| `original_message_at_utc` | Exact original UTC time if available, otherwise JSON `null` |
| `recorded_at_utc` | Actual UTC time this decision record was created; a separate source-file creation time may be stored inside the source file |
| `candidate_sha256` | Exact immutable round-5 candidate file hash |
| `input_manifest_sha256`, `input_versions` | Canonical manifest hash and five ordered exact accession/Item 1 identities |
| `model_identity` | Pinned model and tokenizer revision, four model-file hashes, CPU provider and config hash |
| `metric_contract_sha256` | Hash of the new S3 metric mapping contract, not a replacement for the unchanged round-4 contract |
| `company_failure_policy` | Full-five-company policy from `COMPANY_FAILURE_POLICY`; no subset or filling |
| `axis`, `reference_body_tokens`, `reference_shift`, `boundary_shifts` | `boundary_shift`, `128`, `0`, `[-10,-5,0,5,10]` |
| `pooling` | `equal_chunks`, `body_token_weighted` in the declared order |
| `interpretation_scope` | `local_cpu_fixed_five_company_s3_engineering_grid_only` |

The source file should distinguish the user's original words from the agent's
structured interpretation, retain the original-message time as unknown when
it is unavailable, and record its own actual creation time. Its content is
kept private. The decision record points to its exact bytes rather than
copying the message into a tracked document.

The validator requires the candidate at the expected repository path with
its original SHA-256; the original metric-family contract at its unchanged
hash; and the S3 mapping contract at `docs/S3_BOUNDARY_SHIFT_METRICS.md`.
It compares the record with caller-verified input, model/tokenizer and metric
identities. The runner must independently check actual files, imports,
dependencies and the final tokenizer-only receipt before constructing an
Encoder. A round-4 small-chunk decision has a different kind and scope and
cannot authorize S3. Changing source text, model, shift grid, pooling,
metric meaning or failure policy requires a new human decision. A software
implementation change without a scientific-definition change requires a new
preflight and software checks, but not a fabricated second acceptance.
