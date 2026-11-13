# Round 3 handoff: gated small-chunk software and offline validation

**Date:** 2026-10-31. **Scope:** implementation and local validation of the
new engineering probe's controlled entry point. The candidate remains
`PROPOSED`, `researcher_accepted=false`, and candidate inference `NOT_RUN`.
No M5 protocol was frozen, and no candidate five-company vector or financial
result was produced. A software-test boundary deviation involving real
inference on **synthetic** text is disclosed below; therefore a claim that
this entire round had zero real Encoder sessions would be false.

## Baseline, branch and actual source identity

The isolated development checkout was clean on
`codex/research-plan-round2-20261031` at
`5290f2c3c73a1cb2ed76644518469512f92cac76`. The candidate's actual
SHA-256 was the expected
`a307536980057c227d63fafb11b7b25b4615a2923455717616a7431f05d82388`;
the preserved round 2 v3 machine receipt's actual SHA-256 was
`f0710267080e04c82f8e4685db61c2a6a82f271b304e6978ba5e7fe4c79e3d72`.
This round created local branch `codex/research-plan-round3-20261031` without
reset, clean, stash, merge or push. The source implementation was committed
as `39c13bcdbee05634fbc8248ffd715615cfab3fa9` before regenerating
the final tokenizer-only receipt. The documentation-only closeout commit is
reported by Git; this report does not hash itself.

The active interpreter was CPython 3.11.9 from the user's local Python311
installation. A bare import from outside the checkout resolved `finembed` to
the protected `quant/src/finembed`, while an explicit checkout `src` on
`PYTHONPATH` resolved `finembed/__init__.py`, `model.py` and
`small_chunk_runner.py` to this branch. The runtime guard derives the expected
checkout from the requested candidate file, compares 13 critical imported
module paths and source hashes, and captures interpreter, NumPy 2.4.6,
ONNX Runtime 1.31.0, tokenizers 0.23.3, PyYAML 6.0.3, and the available
provider set. The pinned setting requires `CPUExecutionProvider` and rejects
drift before constructing an ONNX session. It does not download or upgrade
dependencies. Key actual source byte hashes at preflight were:

| File | SHA-256 |
| --- | --- |
| `src/finembed/model.py` | `9398d3ccefc08ba10104dd0a42e584d123aaec77d836d4c2094497a732412445` |
| `src/finembed/sensitivity_preflight.py` | `4d654196aea5c013ee4eb7a18a419d845238ddc916e369d8a0b33730e8af3015` |
| `src/finembed/small_chunk_runner.py` | `414357244e7924809993553d9f7bba3978820434a62b219d9204114a6649c566` |
| `src/finembed/small_chunk_metrics.py` | `58a30b054b51db560b8f566d6b9784c1c3bd4ce56037c7ec9b2d4c0c9517f558` |
| `src/finembed/small_chunk_decision.py` | `70a2f4fbd036212602c78713e59f7e103ed29bb3fa67b3b4bb043836a9411bf8` |
| `src/finembed/small_chunk_provenance.py` | `b383bee52a6e3ee8803b67dd5c5cada35f77a952e4b7a92960d782c5a5eb03a1` |

## Plan, execution, evidence and status

| Planned item | Actual work | Evidence | Deviation / status |
| --- | --- | --- | --- |
| Keep the candidate immutable and define a separate decision | Original candidate bytes unchanged. Added an explicit `NOT_PROVIDED` template and validator for missing, rejected, wrong-scope, version-mismatch and accepted claims. Acceptance binds candidate, ordered five Item 1 versions, model/tokenizer, metric contract and full-case policy. Actual decision/source files must be ignored, untracked and hash-bound. | [Decision guide](../docs/SMALL_CHUNK_DECISION_RECORD.md), [template](../configs/small_chunk_decision_template.json), decision tests | No actual researcher decision was supplied; status `MISSING`. A hash does not authenticate a decision maker. |
| Implement future controlled execution | Added `check`, `run`, and `verify-output` CLI commands. The public `run` path validates current import provenance, receipt bindings, fresh tokenizer-only conclusions, original M1 corpus, model files, complete five-company identity and decision **before** `Encoder`. Each company/length has one chunk encoding; both pooling arms reuse those chunk vectors. A unique ignored output retains chunk/company vectors, manifest, failures and, only for a complete grid, all declared metrics. | [Runner source](../src/finembed/small_chunk_runner.py), [fake-encoder tests](../tests/test_small_chunk_runner.py) | Software implemented. No accepted real candidate run. Complex resume is not implemented; use a new output directory after failure. |
| Define directly computable, complete descriptions | Pure float64 metrics report same-pooling 128 reference vector distance, ten pair changes, upper-triangle Spearman, five Top-2 overlaps, rank-2/3 margins and reference-peer movement; fixed-length pooling contrasts and signed ten-pair length-by-pooling interactions are retained. Exact ties and undefined constant ranks are explicit. | [Metric contract](../docs/SMALL_CHUNK_METRICS.md), [implementation](../src/finembed/small_chunk_metrics.py), synthetic numerical tests | All metrics are descriptive. A formal primary metric, uncertainty design and any broader conclusion remain undecided. |
| Regenerate invalidated tokenizer-only preflight | Old v3 retained. New source-root and semantic checks changed `model.py` and `sensitivity_preflight.py`; four new source modules expanded code inventory from 22 to 26. The fresh v1 receipt binds 65 files and passed five exact inputs, every length, the model limit and 216/216 historical 128 rows. | Ignored new receipt SHA-256 `db3b7ae5dbbf735a143f5ae3a179e808444caf70bfb1147f132cbaeb44182481`; [safe summary](2026-10-31_small_chunk_preflight_round3_summary.md) | `PASS_TOKENIZATION_ONLY` is not candidate inference. The old v3 verifier now requests regeneration because it lacks the explicit candidate-root field. |
| Validate software and historical code behavior | The final explicitly safe suite passed 78 tests with two real-model tests deselected. Fake vectors are labeled `SYNTHETIC_TEST_ONLY`; complete/partial status, tamper refusal and gating were checked. An outside-checkout public `run` with no decision returned `MISSING` (exit 2) and created no output directory. | Ignored `artifacts/round3/20261031-software-checks/final_safe_pytest_v2.log`, SHA-256 `bcde9c4eb44a00243cd22f4a3ebaea3d1b0edb4e46ff6834b63bb6c15a3cdb88`; terminal gate output | Earlier broad regression command violated the no-real-inference test boundary, detailed next. The candidate and financial experiment remain unrun. |

## Validation commands and the boundary deviation

The implemented CLI help was inspected. The tokenizer-only commands actually
run from this checkout, with checkout `src` first on `PYTHONPATH`, were:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m finembed.sensitivity_preflight plan --candidate configs/small_chunk_probe_round2.json --corpus ..\quant\artifacts\runs\20261019T003627Z-d3043c3e\corpus_index.json --historical-map ..\quant\artifacts\runs\20261019T003637Z-d1875b3b\analysis\base_128\chunk_map.jsonl --output artifacts/round3/20261031-small-chunk-tokenization-preflight-v1
python -m finembed.sensitivity_preflight verify --receipt artifacts/round3/20261031-small-chunk-tokenization-preflight-v1/receipt.json
```

The public `run` CLI was also invoked from outside the checkout with the
absolute candidate/corpus/new-receipt paths and **no** `--decision`. It returned
`PROBE_GATE_ERROR: ... MISSING: No decision record was provided`, exit code 2;
the requested output directory did not exist before or after. Thus this
actual candidate entry did not construct an Encoder or run a forward call.
`verify-output` was exercised through fake-output tests. It recomputes pooled
vectors from saved chunks and complete-grid metrics, checks file hashes and
scope confinement. A local manifest plus adjacent SHA sidecar detects
ordinary alteration, but is not an independent signature or authorship proof.

The final safe test command was:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m pytest -q tests/test_small_chunk_runner.py tests/test_sensitivity_preflight.py tests/test_small_chunk_decision.py tests/test_small_chunk_metrics.py tests/test_small_chunk_provenance.py tests/test_m6.py tests/test_m7_tail_diagnostic.py tests/test_analysis.py -k 'not test_real_pinned_tokenizer_does_not_truncate_whole_document and not test_synthetic_text_three_arm_output_with_real_pretrained_model'
```

It reported **78 passed, 2 deselected**. The two excluded tests in
`tests/test_analysis.py` construct the pinned real Encoder and perform
forward passes, even though their text is synthetic. Other real-model tests,
including the M10 ONNX adapter integration, were not part of the safe suite.

**Boundary deviation:** Before the exclusion was added, the related-suite
command included `tests/test_analysis.py` without `-k` and reported
**69 passed**. Both `test_real_pinned_tokenizer_does_not_truncate_whole_document`
and `test_synthetic_text_three_arm_output_with_real_pretrained_model` passed.
Their source constructs one real `Encoder` each and calls `embed_chunks` on
synthetic text, so this round had **two real Encoder constructions and a
nonzero number of real forward calls** during software validation. That
early command did not persist batch-call counters, so its exact actual
forward-call count is `UNKNOWN`; it must not be reported as zero or as a
candidate finding. No original five-company candidate text was encoded by
that test command. No generation, training, retrieval evaluation, financial
prediction, backtest or risk optimization was run in this round.

## Decision and provenance boundaries

The decision template is not a decision, and no actual accepted record is in
the checkout. The execution gate can verify a written source file's presence,
ignored/untracked scope and exact bytes, but software cannot authenticate its
author or interpretation; independently inspect the decision source before
an actual run. The minimal researcher choice is ACCEPT or REJECT for this
exact candidate, five-input and pinned-model identity, complete-case policy,
full descriptive metric contract and narrow engineering inference scope,
with an actual cited source and explicit UTC decision time. A change to text,
model/tokenizer, grid, pooling meaning or metric meaning requires a new
decision. A source/runtime implementation or dependency change without
changed scientific meaning requires a new tokenizer-only preflight and
software review. The final candidate, preflight and decision must all pass
again before future model loading. No runtime estimate or GPU budget is
invented; absent a measured candidate run, both remain `UNKNOWN`.

The colleague's small-step run package remains **supporting artifact not
located**, separately from this new engineering probe. The unspecified JFE
paper is **BLOCKED** pending a title, link or PDF to verify its identity and
claims. University GPU availability, queue and allocation route remain
**NOT_VERIFIED** pending official resource evidence. These gaps do not turn
software fixtures into research data or block this round's local software
preparation. Formal finance still requires the M5 date/decision, point-in-time
text and market vintages, past-only fitting, held-out tests and aligned
baselines. No source text, model weight, generated vector, private decision,
full machine receipt, or test log is committed in this handoff.
