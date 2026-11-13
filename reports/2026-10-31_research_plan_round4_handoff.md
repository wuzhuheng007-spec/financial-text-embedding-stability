# Round 4 handoff: accepted-scope five-company engineering probe

**Date:** 2026-10-31. **Branch:** `codex/research-plan-round4-20261031`.
The verified round 3 baseline was
`917e2990512483dd2625de45dc38d1da4f8e8068`; the round 4 gate and
decision-time code commit is `241ce77ee76e31737e2e67e834d18a987cd79237`.
The final closeout commit is reported in the accompanying transfer inventory
and Git log. No push is reported here.

**Scope:** One version-bound, five-company 10-K Item 1 small-chunk
**engineering probe** with the already pinned MiniLM/ONNX model. This round
did execute that real-model probe. The candidate JSON itself still says
`PROPOSED`, `researcher_accepted=false`, and `real_inference_status=NOT_RUN`
because it is an immutable proposal; a separate private decision record
accepted only the identified execution scope and versions. That record does
not freeze M5, authorize a general scientific claim, or turn the colleague's
unlocated run into a reproduced result.

## Plan, actual work, evidence and status

| Planned item | Actual work | Evidence | Deviation / status |
| --- | --- | --- | --- |
| Retain exact candidate and metric meanings | Candidate SHA-256 stayed `a307536980057c227d63fafb11b7b25b4615a2923455717616a7431f05d82388`; metric contract SHA-256 stayed `58a7b2f1389be691ef979f4fc4710f11d29027617c93f5a4eab580f73f3a6402`. The fixed grid is 118/123/128/133/138 body tokens, zero overlap, and two pooling rules for the same five M1 Item 1 versions. | `configs/small_chunk_probe_round2.json`, `docs/SMALL_CHUNK_METRICS.md`, file-hash readback and run manifest | **UNCHANGED**. Candidate-file status remains `PROPOSED`; the separate version-bound decision is the execution gate. |
| Record the user's bounded decision without inventing a message time | A relayed user message explicitly accepted the identified engineering scope. Researcher/recipient is recorded as `zhuheng wu` as supplied. The original message's UTC timestamp is unknown and recorded as JSON `null`; the private record was created at `2026-10-31T01:17:26.914188Z`. The source and record are ignored, untracked and hash-bound; record SHA-256 is `2e446cf5d5841e346699202afef2db374f69d5e69ec7e2edc4a20e3162efe859`. | Private source/record, [decision contract](../docs/SMALL_CHUNK_DECISION_RECORD.md), `check` log | Validator status **ACCEPTED** for exact bindings. `source_authenticity=NOT_VERIFIED_BY_SOFTWARE`; software does not independently authenticate identity. |
| Regenerate the invalidated offline gate | The round 3 tokenization receipt `db3b7ae5dbbf735a143f5ae3a179e808444caf70bfb1147f132cbaeb44182481` became stale after gate-code changes. A new tokenizer-only receipt passed the exact five inputs, full chunk coverage and the historical 128 map. It binds 65 local files and had zero model forward calls. | Ignored `artifacts/round4/20261031-small-chunk-tokenization-preflight-v1/receipt.json`, SHA-256 `9a046813accd5295992b8074c7fa42c745dfc765ad5bcdcab8f5ad43d9e660e0` | **PASS_TOKENIZATION_ONLY** before the real run. Its generated summary still says model `NOT_RUN` because it is a pre-run snapshot, not the final round status. |
| Check all entry gates before encoder construction | The runner's `check` command returned `VERSION_GATES_PASSED_SOURCE_AUTHENTICITY_NOT_VERIFIED_BY_SOFTWARE`, decision status `ACCEPTED`, and `encoder_session_created=false`. | Ignored `artifacts/round4/20261031-check.log`; [runner](../src/finembed/small_chunk_runner.py) | **PASS** for software/version gates. Independent human interpretation of the decision source is outside the validator. |
| Execute the bounded real-model grid | The run reached `COMPLETE`: 25/25 document-length jobs, 1,083/1,083 chunk vectors, 50/50 pooled company vectors, 145 actual ONNX batch-call attempts, one Encoder construction and one real session, and zero recorded failures. Each document-length job encoded once and reused its chunk vectors for both pooling rules. | Ignored `artifacts/round4/20261031-small-chunk-real5-run-v1/manifest.json`, SHA-256 `698942ddc8a7b395108a3ce2b72deab0f370ed3cd9de2bca827b693632182d5e`; `run-v1.log` | **COMPLETE — REAL_MODEL**, limited to this exact five-text, one-model engineering grid. Counts are recorded executions, unlike the earlier preflight's planned counts. |
| Recompute saved outputs and describe every arm | `verify-output` returned `PASS_SAVED_OUTPUT_RECOMPUTED` for 25 jobs and recomputed complete-grid metrics. The complete metric JSON SHA-256 is `879b9753af91e8f1a1a9248aec4b81577a16c2ac902b33b8b63da3b148d2ac6d`. The descriptive report retains all ten arms, Top-2 set changes, tail-token counts and metric families. With matching input/model/config/hash checks, both 128-token pooling arms had observed maximum absolute vector and cosine-matrix differences of exactly zero versus the saved historical M6 outputs. | Ignored `artifacts/round4/20261031-verify-output-v1.log`; [tracked descriptive report](2026-10-31_small_chunk_probe_descriptive_results.md), SHA-256 `3308783aab453467cf86fc7fbe0a61c59d74da98227e4b227f15634910db42fb`, byte-identical to ignored v2; saved M6 receipt SHA-256 `38d04f96c6452bc367d866b26dcd325b2674c2212bb385aee02b87efec888360` | **DESCRIPTIVE READBACK ONLY**. Equality of the 128 reference is a compatibility check; it is not evidence that other lengths are harmless or a reproduction of the colleague's observation. |
| Validate software and report without additional model calls | Explicitly safe code tests reported **78 passed, 2 deselected**; the excluded tests construct a real Encoder. The separate report tests used synthetic fixtures and reported **7 passed**. | Ignored `artifacts/round4/20261031-safe-code-tests.log`, SHA-256 `ef044b332892f034f159edc30925f26678f4683afc22f5b888c9f21a99f3a04e`; ignored `artifacts/round4/20261031-report-tests.log`, SHA-256 `0424e2574836a984d2848d9f062594b4afa2d2e56ac530569a0f8fccbc669ec0` | **SOFTWARE ONLY** for these tests. Fixtures do not replace the separately saved real-model run. |

Official SEC index-header checks identify the original 10-K accession, filer
and CIK for [AAPL](https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index-headers.html),
[MSFT](https://www.sec.gov/Archives/edgar/data/789019/000095017025100235/0000950170-25-100235-index-headers.html),
[AMZN](https://www.sec.gov/Archives/edgar/data/1018724/000101872425000004/0001018724-25-000004-index-headers.html),
[GOOGL](https://www.sec.gov/Archives/edgar/data/1652044/000165204425000014/0001652044-25-000014-index-headers.html)
and [META](https://www.sec.gov/Archives/edgar/data/1326801/000132680125000017/0001326801-25-000017-index-headers.html).
Those online headers do not verify the extracted Item 1 byte hashes; the local
M1 source chain and preflight provide that separate check.

## Timing, numerical scope and interpretation

The timed `run` wrapper measured **28.5032585 seconds** from
`2026-10-31T01:18:07.1263275Z` to `2026-10-31T01:18:35.6412818Z`, with exit
code zero and `CPUExecutionProvider`. These values come from ignored
`artifacts/round4/20261031-run-v1-timing.json` (SHA-256
`16a7e2c2f28ca54b76cd9689f4a326752695df43a24a59a0313e061705fab171`).
Peak memory is **UNKNOWN**; no GPU runtime or memory number is inferred.

The full metrics retain each arm's company cosine matrix, ten dependent
company-pair values, ordered peers, Top-2 overlap, rank-2/3 gap and tracked
peer movement, plus same-length pooling contrasts and signed descriptive
length-by-pooling interactions. The tracked descriptive report summarizes all
declared arms without selecting a favorable result. Length changes also move
many boundaries, alter the number of chunks and change tail weights. These
observations cannot identify a pure token-count causal effect. Five firms,
one filing version each and one encoder cannot support a population claim;
company pairs are not independent observations. No significance test, danger
threshold, semantic-quality conclusion, prediction, trading-risk magnitude or
economic loss is inferred.

The round 3 handoff's disclosed boundary deviation remains part of the audit
history: two real Encoder tests ran on **synthetic** text during that earlier
software check and made a nonzero number of real forward calls, with exact
batch-call count **UNKNOWN**. They are separate
from this round's 145 recorded candidate-run batch attempts and are not a
small-chunk result. This round did not run generation, training, retrieval
evaluation, return prediction, backtesting or risk optimization.

At run time, `git_at_run.status_porcelain` listed only two untracked, report-only files: `scripts/report_small_chunk_round4.py` and `tests/test_report_small_chunk_round4.py`. Bound `finembed` module hashes and dependencies matched the final preflight during the run and the first saved-output verification; the reporting files were committed during closeout. The final worktree is clean.

A closeout self-audit found that two tracked status documents briefly contained the verbatim private acceptance quote in an unpushed local intermediate commit. The final branch removes that quote from tracked files, while the source record remains ignored. The superseded local Git object may remain recoverable via reflog; no destructive history cleanup was attempted.

## Research integrity and remaining gates

- [x] Exact candidate, contract, input versions, model/tokenizer files,
  current code/runtime and fresh preflight were checked before the real run.
- [x] The private decision record binds the full five-company/failure policy
  and the source bytes; original message time remains unknown rather than
  being replaced by record-creation time.
- [x] Real execution counts, complete-grid status, array hashes and metrics
  were saved and rechecked; failed arms would not have produced full-grid
  metrics.
- [x] The 128-token historical comparison checked matching versions and
  reported observed exact equality without turning it into a risk threshold.
- [ ] Independent authentication of the private decision source's authorship
  and interpretation is not supplied by the software hash check.
- [ ] M5 formal scientific parameters, a held-out design and any financial
  point-in-time data protocol remain unfrozen and unrun.

Three outside evidence gaps remain separate: the colleague's small-step run
package is **supporting artifact not located**; the unspecified JFE paper is
**BLOCKED** until a unique title, DOI, link or PDF can be checked; university
GPU availability/allocation is **NOT_VERIFIED** pending an official source.
None of these gaps changes the identity or limited scope of the saved local
probe. Raw filing text, model weights, vector arrays, private decision source,
machine receipts and logs stay in ignored local artifacts; this tracked
handoff and descriptive report contain only bounded identities and results.
