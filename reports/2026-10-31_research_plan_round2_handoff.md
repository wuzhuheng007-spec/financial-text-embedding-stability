# Research plan review and offline small-chunk preflight — 2026-10-31

**Result:** The [maintained plan](../docs/RESEARCH_EXECUTION_PLAN.md) was corrected and a [new engineering probe candidate](../configs/small_chunk_probe_round2.json) was checked with the local tokenizer only. The [sanitized preflight summary](2026-10-31_small_chunk_preflight_summary.md) and ignored machine receipt report `PASS_TOKENIZATION_ONLY`. The candidate is `PROPOSED`, `researcher_accepted=false`; encoder, generation, retrieval evaluation and finance statuses remain `NOT_RUN`. M5 is not frozen.

## Baseline and review

The development checkout was clean at `codex/research-plan-round1-20261031`, HEAD `ac71f37527a52c57c7f662f5dce112ec2f77311d`, with no round-one remote branch. This round used a new local `codex/research-plan-round2-20261031` branch. Applicable instructions supplied in the conversation were followed; no repository `AGENTS.md` was found. The protected original `quant` workspace and the separate share history were not reset, cleaned, stashed, merged or written.

| First-round issue | Correction in the maintained plan |
| --- | --- |
| S1 combined identical-setting repeat and a possible provider/device change | S1 now fixes provider/device; a provider comparison would be another factor. Fixed-weight repeats are not training seeds. |
| S2 could imply the small-step settings came from the meeting member | The 118/123/128/133/138 grid is explicitly a **new engineering probe**, not a reproduction; the member's supporting artifact remains absent. |
| S4 said saved chunk vectors could be reused without qualifying their length | New lengths require future encoding once per company/length; both pooling arms then share those exact chunk vectors. |
| S7 selected an interaction after seeing S2/S4 outcomes | The candidate grid and descriptive length-by-pooling contrast are declared in advance; no arm is selected by observed effect. |
| Rank-2/3 movement, tie rules and completion gates were underspecified | The plan now gives formulas, denominators, invalidity, completion conditions and the exact ticker tie rule; financial completion remains behind M5. |

The [M6 sensitivity diagnostic](../results/m6_real5_20261025/sensitivity_diagnostics.json) already contains the four historical 128/192 × pooling settings, rank-2/3 margins, MSFT–AMZN/META cosines and tail information. Its private supplemental counterpart was byte-identical (SHA-256 `3538b921c6c23c5c368d5d6377532fd01ef39fd0a1eeb40aa8f4920c8a42c94a`); this round did not recompute those values. The [M0–M7](../docs/EVIDENCE_TRACE_M0_M7.md) and [M8–M10](../docs/EVIDENCE_TRACE_M8_M10.md) traces, [M5 draft](../docs/M5_PROTOCOL_DRAFT.md), saved M6/M7 outputs, and chunking/pooling/metric code were read for scope. The 10-K company-vector task remains separate from M10 passage retrieval.

## Candidate and tokenization preflight

The candidate pins the exact five M1 original 10-K Item 1 texts, `configs/smoke.yaml` model/tokenizer identity, body lengths 118/123/128/133/138, zero overlap and equal/body-token-weighted pooling. It neither changes the historical 128/192 smoke config nor fits the existing inference CLI's strict 128/192 parser. No alternative text or reduced company subset was substituted.

`src/finembed/model.py` now exposes the same chunk partition logic to an offline caller while `Encoder.chunks` delegates to it. `src/finembed/sensitivity_preflight.py` validates the M1 raw/primary/Item 1 lineage, streams SHA-256 over the four pinned model files without constructing `Encoder` or an ONNX session, uses only the local tokenizer with default truncation and padding disabled, checks coverage, spans, special-token budget, positive pooling weights and historical mapping, and writes a new ignored receipt. The saved 128-token M2/M3 map matched all 216 rows, including token IDs and source spans. Every candidate length passed full coverage; the maximum planned model input is 140 of 256 tokens including specials. The final ignored receipt is `artifacts/round2/20261031-small-chunk-tokenization-preflight-v3/receipt.json` (SHA-256 `f0710267080e04c82f8e4685db61c2a6a82f271b304e6978ba5e7fe4c79e3d72`). It binds 60 file entries, including exact inputs, candidate/config, model/tokenizer, historical map and source code. The generated summary was supplemented with receipt provenance in the linked, credential-free report above.

The receipt's 25 company-by-length encoding jobs, 1,083 chunk vectors and 50 company vectors are **future planned counts**, computed from tokenizer-only chunk counts and two pooling rules. Actual encoder forward calls and model vectors this round: **zero**. Future time and peak memory are `UNKNOWN`; the first-round hardware snapshot was not a benchmark. Preliminary local `-v1` and `-v2` preflight receipts were retained; subsequent preflight and config-code edits made each fail `verify` with one changed bound file. The final `-v3` receipt passed `verify` unchanged. This demonstrates the intended fail-closed binding check without overwriting historical artifacts.

## Commands actually used and validation

From the isolated checkout, the usable tokenizer-only commands are:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m finembed.sensitivity_preflight plan --candidate configs/small_chunk_probe_round2.json --corpus ..\quant\artifacts\runs\20261019T003627Z-d3043c3e\corpus_index.json --historical-map ..\quant\artifacts\runs\20261019T003637Z-d1875b3b\analysis\base_128\chunk_map.jsonl --output artifacts/round2/next-tokenization-preflight
python -m finembed.sensitivity_preflight verify --receipt artifacts/round2/next-tokenization-preflight/receipt.json
```

The final run used unique output directory `artifacts/round2/20261031-small-chunk-tokenization-preflight-v3`; the command refuses an existing output directory. The package installed in the current Python environment points at protected `quant`, so this checkout's `src` was explicitly placed first on `PYTHONPATH`. The existing inference CLI was also called with the proposed JSON candidate and returned `CONFIG_ERROR: Proposed engineering candidate is plan-only and cannot be an inference config` before a run or encoder session was created. Other checks included `git status/rev-parse/remote`, source and config reads, local SHA-256 comparisons, and:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m pytest -q tests/test_sensitivity_preflight.py tests/test_m6.py
```

The focused software run reported **28 passed**; its ignored log is `artifacts/round2/20261031-software-checks/pytest_focused.log` (SHA-256 `47b3dee5108bbe3a7beeeab10530b2a4cf0a1764506897c12a35aed8fc29d121`). The new synthetic tests cover complete token ranges, model limit and empty input rejection, pooling arithmetic, plan-only behavior without `Encoder`, the inability to pass a proposed JSON candidate to the inference config loader, and input/config/code change rejection. They verify software behavior only. No broad test suite or existing real-model test was run because that would violate this round's no-inference boundary. Relative Markdown links, table structure, `git diff --check` and the staged-file scope were checked before the local documentation/code commit.

## Remaining gates

1. The researcher must decide whether to accept this **engineering probe** grid and its fixed five-text/model scope, metrics, complete-case policy and output retention before any real inference. Candidate preparation does not approve M5 or authorize a formal population claim.
2. An inference runner for this candidate is **not implemented**. The existing 128/192 `analyze` and M6 entry points reject the proposed JSON settings. Any future runner must require an explicit version-bound researcher acceptance record, revalidate all receipt bindings and fail closed on changed input/config/code before model loading.
3. The meeting member's small-step run package is still **supporting artifact not located**. It is not needed for this new probe's preparation and is not a defect of the share branch.
4. The JFE paper remains **BLOCKED** until its title, link or PDF is supplied; university GPU availability remains **NOT_VERIFIED** until an official allocation or resource record is available. Neither gap was filled by inference from the meeting transcript.
5. Formal finance still requires the dated M5 protocol, first-public/revision evidence, tradable decision clock, past-only fitting, aligned baselines, held-out split and separate predictive versus net-economic outcomes.

No source text, model weight, index, private meeting file, reviewer reply, credential or ignored preflight receipt is in this commit. The protected source and all historical results remain unchanged.
