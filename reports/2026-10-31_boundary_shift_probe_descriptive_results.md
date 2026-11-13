# S3 boundary-shift engineering probe: descriptive results

**Scope.** One complete fixed-five-company, fixed-MiniLM/ONNX CPU grid. The experimental axis is an internal boundary shift at a constant 128-token base step, not a change in nominal chunk length. This is exploratory after round 4. It does not establish a pure semantic-boundary cause, general model stability, semantic quality, financial prediction, trading loss or economic value.

Saved run: `20261031-s3-real5-run-v1`. Saved-output verification: `PASS_SAVED_OUTPUT_RECOMPUTED`; run status: `COMPLETE`; evidence class: `REAL_MODEL`.

| Bound object | SHA-256 or identity |
| --- | --- |
| Manifest | `eab4abec64f65d9f9d48589eba20c59edc5c75f4d6ee2567db6765e58dfd1f47` |
| Metrics | `77e4bcdd7074a0206ccf798e4770106177b2409c57ecef02a7ef3d51b0b22f27` |
| S3 tokenizer-only receipt | `3d9bdc3668de1c10eb6c23f49b14cd35995f7d38f7af61aa59be4f85f5aad0d3` |
| Candidate | `a16dc7786e33c607eabb833eb1adb7f8c9652735cfb21eaa862f12a3661199e2` |
| S3 metric contract | `fc4c55f23d1c2bbe29967ca657a97c38afa5456258355b958c8fc2d5e21051c9` |
| Private decision record hash only | `f29243ea723a928f660af5e0fd58eb64f15169d0cd6ee4e0478fe0a7d5619303` |
| Input manifest | `c240d12864bdbe95f21586d4068218a2d3623e1372ba403d521a01fdfdb82a4e` |
| Execution code commit | `ef6a0a89e7a4d6f65114110cd640fc88c6cd84c3` |
| Model revision | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` |
| Provider / batch size | `CPUExecutionProvider` / `8` |

**Ordered source versions.** AAPL `0000320193-25-000079` Item 1 `68e0a75303e8c595642a7bbae95f025aac556ada3d1b6b10b0b29fb745cb99ec`; MSFT `0000950170-25-100235` Item 1 `4ac1db966b4ea0de05cc3916b20bac2c6616002804540e392338cf719ebb1da6`; AMZN `0001018724-25-000004` Item 1 `121f153d97d010b2036acd3a001173c7c8a5426c1582bb98560f6cce8109f21b`; GOOGL `0001652044-25-000014` Item 1 `7e2adaf0c7f14e5c7d28b8d4597fd268398324994569fe9e196582d0665fb69b`; META `0001326801-25-000017` Item 1 `647a4c678beb67ccce0ffdac9dde429f3c5be336281fd58a5bb972d624b74ede`.

**Actual execution.** 25/25 document-shift jobs; 1080/1080 chunk vectors; 50/50 company vectors; 1 real Encoder/ONNX session; 145 actual batch-call attempts. The two pooling arms reuse each document-shift job's chunk vectors. Measured process wall time from output-directory creation: 9.663972 s; peak memory: UNKNOWN (not measured).

## All ten shift-by-pooling arms

Delta zero is the reference for within-pooling shift comparisons; its self-comparison is identically zero and is not presented as a separate effect arm. Each row has five firms. All chunks were encoded once per company and delta.

| Boundary shift (body tokens) | Pooling | Jobs | Chunk vectors | Company vectors | Mean rank-2/3 score gap | Exact cutoff ties |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| -10 | `equal_chunks` | 5 | 216 | 5 | 0.0298375543 | 0 |
| -10 | `body_token_weighted` | 5 | 216 | 5 | 0.0306500722 | 0 |
| -5 | `equal_chunks` | 5 | 216 | 5 | 0.0284704398 | 0 |
| -5 | `body_token_weighted` | 5 | 216 | 5 | 0.0299665728 | 0 |
| +0 | `equal_chunks` | 5 | 216 | 5 | 0.0289253737 | 0 |
| +0 | `body_token_weighted` | 5 | 216 | 5 | 0.0301746957 | 0 |
| +5 | `equal_chunks` | 5 | 216 | 5 | 0.034741129 | 0 |
| +5 | `body_token_weighted` | 5 | 216 | 5 | 0.035349301 | 0 |
| +10 | `equal_chunks` | 5 | 216 | 5 | 0.0332058066 | 0 |
| +10 | `body_token_weighted` | 5 | 216 | 5 | 0.0340882402 | 0 |

## Nonzero shifts against delta zero within the same pooling

Each signed pair change is candidate cosine minus its same-pooling delta-zero cosine. Absolute pair changes and Spearman use ten dependent company pairs; Top-2 overlap averages five firms.

| Delta | Pooling | Mean vector cosine distance | Mean signed pair change | Mean absolute pair change | Max absolute pair change | Pair-rank Spearman | Mean Top-2 overlap | Mean absolute tracked-rank shift | Mean rank-2/3 gap change |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| -10 | `equal_chunks` | 0.00260533893 | -0.000219735827 | 0.00479403471 | 0.00907802978 | 0.939393939 | 1 | 0.2 | 0.000912180575 |
| -10 | `body_token_weighted` | 0.00252607786 | 0.000273853539 | 0.00480049512 | 0.00862981352 | 0.951515152 | 1 | 0.2 | 0.000475376479 |
| -5 | `equal_chunks` | 0.00199287402 | 4.93803364e-05 | 0.00390541441 | 0.00849461779 | 0.987878788 | 1 | 0 | -0.000454933948 |
| -5 | `body_token_weighted` | 0.00187747827 | 0.000150406527 | 0.00404201548 | 0.00840087753 | 1 | 1 | 0 | -0.000208122926 |
| +5 | `equal_chunks` | 0.0017219882 | -0.00212898853 | 0.00529353894 | 0.00828249683 | 0.987878788 | 1 | 0.1 | 0.00581575526 |
| +5 | `body_token_weighted` | 0.00167937599 | -0.00218814693 | 0.00507403858 | 0.00815076103 | 0.963636364 | 1 | 0.1 | 0.00517460535 |
| +10 | `equal_chunks` | 0.00216522066 | 0.00241224234 | 0.00471439637 | 0.0121407194 | 0.987878788 | 1 | 0.1 | 0.00428043288 |
| +10 | `body_token_weighted` | 0.00211129605 | 0.00223046439 | 0.00450632795 | 0.0121024524 | 0.951515152 | 1 | 0.1 | 0.00391354449 |

## Pooling comparison at every fixed shift

`body_token_weighted` minus `equal_chunks` uses the same stored chunk vectors within each company and shift.

| Delta | Mean vector cosine distance | Mean signed pair change | Mean absolute pair change | Max absolute pair change | Pair-rank Spearman | Mean Top-2 overlap | Mean absolute tracked-rank shift | Mean rank-2/3 gap change |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| -10 | 0.000121530778 | -0.000838089641 | 0.00134276466 | 0.00350601127 | 1 | 1 | 0 | 0.000812517872 |
| -5 | 0.00013798444 | -0.00123065282 | 0.00159045661 | 0.00342513573 | 1 | 1 | 0 | 0.00149613299 |
| +0 | 0.000157933394 | -0.00133167901 | 0.00198562669 | 0.00448660265 | 0.987878788 | 1 | 0 | 0.00124932197 |
| +5 | 0.000191152591 | -0.00139083741 | 0.00199694661 | 0.00432776275 | 0.963636364 | 1 | 0 | 0.000608172055 |
| +10 | 0.000220959078 | -0.00151345696 | 0.00201160891 | 0.00447555178 | 0.987878788 | 1 | 0 | 0.000882433579 |

## Signed shift-by-pooling interaction

For every nonzero delta and dependent pair, `I=[S(delta,weighted)-S(0,weighted)]-[S(delta,equal)-S(0,equal)]`. Neither absolute changes nor causal effects enter this signed difference of contrasts.

| Delta | Mean signed interaction | Minimum / maximum pair interaction | Dependent pair count |
| ---: | ---: | --- | ---: |
| -10 | 0.000493589367 | -0.000913687025 / 0.00227225321 | 10 |
| -5 | 0.000101026191 | -0.00134674885 / 0.00106146692 | 10 |
| +5 | -5.91584015e-05 | -0.000759416976 / 0.000617801982 | 10 |
| +10 | -0.000181777952 | -0.00190892561 / 0.00108565165 | 10 |

All declared signed company-pair interactions:

| Delta | Company pair | Equal signed shift effect | Weighted signed shift effect | Weighted minus equal interaction |
| ---: | --- | ---: | ---: | ---: |
| -10 | AAPL / MSFT | 0.00738718425 | 0.00744797225 | 6.07880005e-05 |
| -10 | AAPL / AMZN | 0.00596303083 | 0.00646760951 | 0.000504578676 |
| -10 | AAPL / GOOGL | -0.000187740127 | 0.00208451308 | 0.00227225321 |
| -10 | AAPL / META | 0.00292743987 | 0.00201375284 | -0.000913687025 |
| -10 | MSFT / AMZN | -0.0043009519 | -0.00305828764 | 0.00124266426 |
| -10 | MSFT / GOOGL | -0.00907802978 | -0.00862981352 | 0.000448216266 |
| -10 | MSFT / META | -0.00325070261 | -0.00379160717 | -0.000540904567 |
| -10 | AMZN / GOOGL | -0.00825142827 | -0.00715349956 | 0.00109792872 |
| -10 | AMZN / META | 0.00150659644 | 0.00129006118 | -0.000216535253 |
| -10 | GOOGL / META | 0.00508724304 | 0.00606783441 | 0.000980591376 |
| -5 | AAPL / MSFT | -0.00138955536 | -0.00151352599 | -0.000123970633 |
| -5 | AAPL / AMZN | 0.00344250002 | 0.00348823991 | 4.57398894e-05 |
| -5 | AAPL / GOOGL | -0.00534480715 | -0.0047290107 | 0.00061579645 |
| -5 | AAPL / META | -0.00182333579 | -0.00317008464 | -0.00134674885 |
| -5 | MSFT / AMZN | 0.0056682887 | 0.00644975619 | 0.000781467496 |
| -5 | MSFT / GOOGL | -0.00849461779 | -0.00840087753 | 9.37402644e-05 |
| -5 | MSFT / META | 0.00194185649 | 0.0014982645 | -0.000443591988 |
| -5 | AMZN / GOOGL | -0.00222785429 | -0.0016445459 | 0.000583308392 |
| -5 | AMZN / META | 0.00778032968 | 0.00752338366 | -0.000256946024 |
| -5 | GOOGL / META | 0.000940998862 | 0.00200246578 | 0.00106146692 |
| +5 | AAPL / MSFT | 0.00828249683 | 0.00815076103 | -0.000131735799 |
| +5 | AAPL / AMZN | 0.00151514138 | 0.00101300036 | -0.000502141018 |
| +5 | AAPL / GOOGL | -0.00464476446 | -0.00516433767 | -0.000519573206 |
| +5 | AAPL / META | -0.0067643029 | -0.00687175951 | -0.000107456613 |
| +5 | MSFT / AMZN | 0.00602511383 | 0.00526569685 | -0.000759416976 |
| +5 | MSFT / GOOGL | -0.00602978723 | -0.00566902778 | 0.000360759448 |
| +5 | MSFT / META | -0.00418703779 | -0.00376572211 | 0.000421315682 |
| +5 | AMZN / GOOGL | -0.00460428893 | -0.00473426634 | -0.000129977413 |
| +5 | AMZN / META | -0.00545035345 | -0.00483255146 | 0.000617801982 |
| +5 | GOOGL / META | -0.00543210261 | -0.00527326271 | 0.000158839898 |
| +10 | AAPL / MSFT | 0.0121407194 | 0.0121024524 | -3.8266985e-05 |
| +10 | AAPL / AMZN | 0.00605632938 | 0.00516775724 | -0.000888572144 |
| +10 | AAPL / GOOGL | 0.000824345916 | 0.00163675864 | 0.000812412723 |
| +10 | AAPL / META | -0.000684972992 | -0.00142568467 | -0.000740711681 |
| +10 | MSFT / AMZN | 0.00823952687 | 0.00633060126 | -0.00190892561 |
| +10 | MSFT / GOOGL | -0.00530531677 | -0.00421966512 | 0.00108565165 |
| +10 | MSFT / META | -0.0016896362 | -0.0016515557 | 3.80804997e-05 |
| +10 | AMZN / GOOGL | 0.000373333993 | -0.000576252041 | -0.000949586035 |
| +10 | AMZN / META | -0.0038308442 | -0.00350616027 | 0.000324683928 |
| +10 | GOOGL / META | 0.007998938 | 0.00844639214 | 0.000447454139 |

## Every Top-2 peer-set change

The reference is delta zero under the same pooling. Peer order and rank-2/3 cutoff gaps are shown separately. The table includes every firm and nonzero-shift arm whose Top-2 *set* changed.

| Delta | Pooling | Firm | Reference Top-2 | Shifted Top-2 | Reference rank 2 / rank 3 (gap) | Shifted rank 2 / rank 3 (gap) |
| ---: | --- | --- | --- | --- | --- | --- |
| — | — | None | — | — | — | — |

Top-2 set-change rows by firm: AAPL 0, MSFT 0, AMZN 0, GOOGL 0, META 0; total 0. These are dependent firm/arm contrasts, not independent samples.

## Every same-delta pooling Top-2 peer-set change

At each fixed boundary shift, `equal_chunks` is the reference and `body_token_weighted` is the candidate. This table lists every firm whose Top-2 peer set changes under that pooling contrast, including delta zero.

| Delta | Firm | Equal Top-2 | Weighted Top-2 | Equal rank 2 / rank 3 (gap) | Weighted rank 2 / rank 3 (gap) |
| ---: | --- | --- | --- | --- | --- |
| — | None | — | — | — | — |

Same-delta pooling set-change rows by firm: AAPL 0, MSFT 0, AMZN 0, GOOGL 0, META 0; total 0. The pooling contrast reuses identical stored chunk vectors within each job.

## First and tail body lengths

Each cell is `first/tail` body token IDs from the tokenizer-only receipt, not an inferred model effect. Complete interior chunks remain at the 128-token base step.

| Firm | Total body IDs | Chunks per delta | -10 | -5 | 0 | +5 | +10 |
| --- | ---: | ---: | --- | --- | --- | --- | --- |
| AAPL | 2916 | 23 | 118/110 | 123/105 | 128/100 | 133/95 | 138/90 |
| MSFT | 8489 | 67 | 118/51 | 123/46 | 128/41 | 133/36 | 138/31 |
| AMZN | 2552 | 20 | 118/130 | 123/125 | 128/120 | 133/115 | 138/110 |
| GOOGL | 5800 | 46 | 118/50 | 123/45 | 128/40 | 133/35 | 138/30 |
| META | 7577 | 60 | 118/35 | 123/30 | 128/25 | 133/20 | 138/15 |

Equal pooling uses `1/n` for every chunk. Body-token weighted pooling uses the *actual* shifted chunk length divided by the full body-token count, so first and tail weights also vary with delta. AMZN's negative-shift long tail remains intact if within the pinned model budget.

## Failures, exclusions and undefined measures

Failures: **0**. Excluded firms/arms: **0**. A partial grid would have no full-grid metrics file.

Undefined Spearman comparisons: **0**.

## Interpretation boundary

The metric file retains every company vector distance, signed and absolute pair change, all five-firm cosine matrices, ordered neighbors, rank-2/3 identities and gaps, tracked reference-neighbor movement, exact ties and undefined reasons. The ten company pairs share firms. No p-value, danger cutoff or selected default arm follows.

S3 keeps the full text and chunk count, but moves chunk content, first/tail lengths and weighted-pooling weights. The result cannot isolate a pure semantic-boundary cause. No training, generation, MoE, new retrieval evaluation or formal financial experiment was run by this probe.

## Execution timing and round-4 delta-zero compatibility

The single real S3 run command exited 0 and took 28.878761 seconds of command wall time, including the pre-Encoder checks. Its ignored log SHA-256 is `e9f4ad41afbe00956d89feeeda560cb72ae4f8699149017f042023ca8e209f54`. The manifest's 9.663972-second interval begins only after output-directory creation. The separate saved-output verification command exited 0; its ignored log SHA-256 is `49a4c10e0daa1160a889beb82ed8f8bb9f5883671f67d3fbe6b40e626baf5d8d`. Peak memory was not measured (`UNKNOWN`); this was CPU inference, not a GPU benchmark.

The round-4 manifest, metrics and preflight receipt hashes remain unchanged. Its saved output passed `verify-output` on the round-5 base before the new S3 source inventory was added. A read-only compatibility audit then checked 16 input, model, runtime, normalization and delta-zero chunk-map conditions; all passed. Across five firms, the saved delta-zero chunks and both pooling vectors matched the corresponding round-4 128-token arrays in all 15 comparisons, with maximum absolute array difference 0. Both full delta-zero arm descriptions, including cosine matrices and peer orders, were equal. The ignored vector audit SHA-256 is `ada0938ecdbc5f11ba46707717258dc2a35e5f1007b81366888ca39da0e4589a`; the metric audit SHA-256 is `6f26a3932ddd5cf1e0eeaf79edaaecf73240aa3cb409d3ea877e0ada1d225755`. The old round-4 receipt was not rerun under the changed source inventory. This is a compatibility readback of this project's reference, not a reproduction of the colleague's unlocated run.
