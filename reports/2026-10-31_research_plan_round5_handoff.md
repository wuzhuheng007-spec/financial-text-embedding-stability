# Round 5 handoff: S3 boundary-shift preparation

**Date:** 2026-10-31. **Branch:** `codex/research-plan-round5-20261031`.
The verified round 4 baseline is `c59cc1895deb48946703b96ea4423c2fe55cf0d9`.
The fixed S3 preflight-code commit is `0ed3585d998422228307ffc69c1d2c9812ae2441`.
The final documentation commit, if any, is identified by the accompanying
transfer inventory and Git log; no future SHA is asserted here. No push or
merge is reported.

**Outcome:** The proposed S3 candidate passed tokenizer-only preflight and
independent saved-receipt readback. It remains `PROPOSED`,
`researcher_accepted=false`, and `real_inference_status=NOT_RUN`. This round
created **zero** S3 Encoder sessions, model forward calls and model vectors.

## Plan, execution, evidence and status

| Planned item | Actual execution | Evidence | Deviation / status |
| --- | --- | --- | --- |
| Review the saved round 4 engineering grid | Ran `verify-output` on the retained output **before** adding the S3 module; it recomputed the complete saved grid. Reviewed all ten arms and retained the original report. | Ignored round 4 readback log SHA-256 `b6019bbb446e1529ec87cddf5dde3d4ccbc85dfa460166c4423ab1991fb89504`; [descriptive results](2026-10-31_small_chunk_probe_descriptive_results.md) | **PASS_SAVED_OUTPUT_RECOMPUTED** at the verified baseline. No model rerun. |
| Prepare a separate S3 candidate | Created the five-input, fixed-model, CPU, zero-overlap, 128-base internal-cut shift candidate at deltas `-10,-5,0,5,10`, retaining both existing pooling rules. | [Candidate](../configs/boundary_shift_probe_round5.json), SHA-256 `a16dc7786e33c607eabb833eb1adb7f8c9652735cfb21eaa862f12a3661199e2` | **PROPOSED / NOT ACCEPTED / NOT_RUN**. The round 4 decision does not extend to S3. |
| Validate exact token coverage and input budget without model loading | All 25 company-by-shift preflight cells passed. Each company retained its chunk count across shifts; all body token IDs were covered exactly once. Actual encoded input length peaked at 140 including two special tokens, below the pinned 256-token limit. The delta-zero map matched 216 historical rows. | Ignored receipt SHA-256 `752155f0232efd76dab6ef308a7775003b923057edcd0b03793021c368a52b74`; ignored concise summary; independent audit SHA-256 `4daef4c5cb818b49f8d6ddd13aae78fbc915ab8d4d984723d224d1e62aac3964` | **PASS_TOKENIZATION_ONLY**. The receipt binds 66 local files, including 27 code files; its verification returned `PASS_BOUND_FILES_AND_TOKENIZATION_UNCHANGED`. |
| Check the new pure software behavior | Ran an explicitly selected test suite for S3 cuts, earlier sensitivity preflight and source provenance. | Ignored test log SHA-256 `7c97a55b6e3f3e0c393786a8eb5cc323634e6655b9cb189862823f6fa96cb996` | **22 passed**; synthetic fixtures only. No unfiltered or real-Encoder pytest suite was run. |
| Preserve scope and status in research documents | Updated the maintained plan and task/roadmap ledger for round 4 S2/S4/S7 findings and exploratory S3 status. | [Research plan](../docs/RESEARCH_EXECUTION_PLAN.md), [TASKS](../TASKS.md), [ROADMAP](../ROADMAP.md) | S3 model execution and formal finance remain **NOT_RUN**; M5 remains unfrozen. |

## What the round 4 outputs support

S2 is complete as a fixed-five-company, fixed-MiniLM/ONNX **engineering**
length grid; S4 is complete as a within-grid comparison of `equal_chunks` and
`body_token_weighted`; and S7's declared signed descriptive interaction was
calculated. The [round 4 result report](2026-10-31_small_chunk_probe_descriptive_results.md)
retains all ten length-by-pooling arms and their denominators. For example,
the saved Top-2 set changed for MSFT in four firm/arm contrasts; that
observation is specific to this grid and is not a general susceptibility
claim. No arm is promoted to a default method after viewing the outcomes.

Editing the full filing body, changing chunk length and thereby multiple
boundaries/chunk counts/tails, and changing pooling on fixed chunk vectors are
distinct contrasts. Round 4 does not isolate a pure token-count effect, a
semantic boundary mechanism, or the effect of editing full text. It does not
validate the colleague's unlocated run package. One encoder, five filing
versions and dependent company pairs cannot establish population stability,
semantic improvement, predictive performance, trading risk or economic value.

## S3 candidate and measured tokenization preflight

For `T` complete Item 1 body token IDs, let `n=ceil(T/128)`. Keep endpoints
`0,T` and move every internal cut to `128*j+delta` for `j=1,...,n-1`, with
`delta` in `{-10,-5,0,5,10}`. Slice the original token-ID sequence between
adjacent cuts exactly once. The first length is `128+delta`, complete interior
lengths are 128, and the tail is the delta-zero tail minus `delta`. There is
no retokenization, clipping, splitting, padding, overlap or reordering. Both
pooling rules use the actual resulting body lengths. Invalid cuts, zero-length
segments, incomplete coverage or a model-input budget violation fail the
preflight. The pinned model is `sentence-transformers/all-MiniLM-L6-v2` at
revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` with
`CPUExecutionProvider`; the tokenizer JSON SHA-256 is
`be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037`,
and ONNX weight-file SHA-256 is
`6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452`.
The unchanged round 4 metric contract SHA-256 is
`58a7b2f1389be691ef979f4fc4710f11d29027617c93f5a4eab580f73f3a6402`.

Each entry below is the **tokenizer-only** first/tail body length at deltas
`-10,-5,0,5,10`, respectively. The source accession and Item 1 SHA-256 for
each company are bound in the ignored receipt and the [candidate](../configs/boundary_shift_probe_round5.json).

| Company | Body token IDs `T` | Chunks per delta | First/tail lengths at `-10, -5, 0, 5, 10` |
| --- | ---: | ---: | --- |
| AAPL | 2,916 | 23 | 118/110, 123/105, 128/100, 133/95, 138/90 |
| MSFT | 8,489 | 67 | 118/51, 123/46, 128/41, 133/36, 138/31 |
| AMZN | 2,552 | 20 | 118/130, 123/125, 128/120, 133/115, 138/110 |
| GOOGL | 5,800 | 46 | 118/50, 123/45, 128/40, 133/35, 138/30 |
| META | 7,577 | 60 | 118/35, 123/30, 128/25, 133/20, 138/15 |

AMZN's `delta=-10` tail has 130 **body** tokens by rule, plus two specials
for 132 actual model-input tokens; it was neither clipped nor split. The
largest observed preflight input was 140 tokens, including specials. The
delta-zero candidate map matched the saved historical 128-token map in all
216 rows. Independent readback also matched the round 4 reference chunk-map
hashes for all five companies. Future **plans only** are 25 document-shift
encoding jobs, 1,080 chunk vectors and 50 company vectors. They are not
executed quantities or S3 model results.

## Commands and source identity

All commands below used Python 3.11 from the verified environment with
`$env:PYTHONPATH=(Resolve-Path src).Path`. Only protected `quant` absolute
input arguments are redacted in this tracked report; their exact resolved
paths, file hashes and source identities remain in the ignored local receipt.
The option names, other arguments and recorded exit statuses are unchanged.

```powershell
python -m finembed.small_chunk_runner verify-output --output artifacts/round4/20261031-small-chunk-real5-run-v1
# exit 0; executed before the new S3 source module existed
python -m pytest -q tests/test_boundary_shift_preflight.py tests/test_sensitivity_preflight.py tests/test_small_chunk_provenance.py
# exit 0; 22 passed; selected software tests only
python -m finembed.boundary_shift_preflight plan --candidate configs/boundary_shift_probe_round5.json --corpus <protected-M1-corpus-index> --historical-map <protected-M1-base-128-map> --output artifacts/round5/20261031-boundary-shift-tokenization-preflight-v1
# exit 0; PASS_TOKENIZATION_ONLY
python -m finembed.boundary_shift_preflight verify --receipt artifacts/round5/20261031-boundary-shift-tokenization-preflight-v1/receipt.json
# exit 0; PASS_BOUND_FILES_AND_TOKENIZATION_UNCHANGED
```

The preflight receipt records code commit
`0ed3585d998422228307ffc69c1d2c9812ae2441`, 27 bound code files
and the actual `finembed` import sources beneath this branch's `src`.
The new [preflight implementation](../src/finembed/boundary_shift_preflight.py)
has SHA-256 `c27e2ad6d624042604c2b623c5dddf56da84f4def94148126d2fffd36ec58b76`;
its [focused test](../tests/test_boundary_shift_preflight.py) has SHA-256
`84b88ae765e21e92e7057185b7fe25f6815c9405ed8181c65424c15ef1e28756`.
The ignored plan and verify logs have SHA-256
`cb9ba845c4cbc3f58546f13b266bd386215d4a3c4d11605913884c7972f09a2a`
and `29eb8629e756a8e40e1172e40ddb035e8a53afa7309938db3d9d464942439a41`,
respectively. The old round 4 preflight receipt remains untouched; the
expanded round 5 code inventory may make it stale for a new bound execution
under current code. That does not change the saved round 4 outputs or the
successful pre-change readback.

## Future comparison and remaining decision

If separately accepted for the exact S3 versions and scope, future output
could compare each nonzero `delta` with `delta=0` under the **same** pooling
for company-vector distance, cosine-matrix pair changes, upper-triangle
Spearman, Top-2 overlap and rank-2/3 identities and gaps. It could also
compare pooling at each fixed delta. Keep the complete descriptive metric
family, exact tie/non-finite rules and denominators from the unchanged
contract. Company pairs remain dependent. This is an exploratory comparison
defined after seeing round 4 outcomes, not a confirmatory test. Moving cuts
also changes first/tail lengths and weighted-pooling weights, so a future
effect cannot be labeled a pure semantic-boundary causal effect.

The round 4 `delta=0` saved vectors may be *considered* for reuse only after
checking the exact ordered accession/Item 1 hashes, complete token IDs and
row-by-row cut map, model and tokenizer file hashes/revision, CPU provider,
chunk and document normalization order, pooling implementation, code/source
binding and saved vector-file hashes. Reuse was **not executed** in this
round and would not count as new inference. A new, version-bound researcher
decision is required for S3 model execution; the round 4 acceptance remains
limited to its own length grid. No new identity-authentication workflow is
introduced.

The round 3 deviation remains on record: an earlier regression command
inadvertently included two real-Encoder tests on synthetic text, producing a
nonzero but precisely uncounted number of forward batches. It is not an S3
result. The round 4 audit also retains the transient inclusion of a private
acceptance quote in an unpushed intermediate local commit; the quote is not
repeated here, and no history rewrite or cleanup was attempted.

The colleague's small-step run package remains **supporting artifact not
located**; the unspecified JFE paper identity remains **BLOCKED** pending a
unique checkable citation; official university GPU availability remains
**NOT_VERIFIED**. These independent gaps do not invalidate this local
software/tokenization preparation, and none authorizes new research claims.
Raw filings, model weights, vectors, private decisions, full machine receipts
and logs are excluded from this tracked handoff and shareable transfer.
