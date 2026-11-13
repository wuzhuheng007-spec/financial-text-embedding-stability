# Small-chunk probe descriptive results

**Scope.** This report describes one complete, saved real-model engineering probe after `verify-output` recomputed its arrays and metrics. It does not establish general stability, semantic improvement, a causal token-count effect, statistical significance, prediction, trading risk or economic value. The source decision's authorship still requires independent human review.

Run directory: `20261031-small-chunk-real5-run-v1`. Verification status: `PASS_SAVED_OUTPUT_RECOMPUTED`. Run status: `COMPLETE`; evidence class: `REAL_MODEL`.

| Bound object | SHA-256 or identity |
| --- | --- |
| Manifest | `698942ddc8a7b395108a3ce2b72deab0f370ed3cd9de2bca827b693632182d5e` |
| Metrics | `879b9753af91e8f1a1a9248aec4b81577a16c2ac902b33b8b63da3b148d2ac6d` |
| Tokenization preflight receipt | `9a046813accd5295992b8074c7fa42c745dfc765ad5bcdcab8f5ad43d9e660e0` |
| Candidate | `a307536980057c227d63fafb11b7b25b4615a2923455717616a7431f05d82388` |
| Decision record | `2e446cf5d5841e346699202afef2db374f69d5e69ec7e2edc4a20e3162efe859` |
| Input manifest | `c240d12864bdbe95f21586d4068218a2d3623e1372ba403d521a01fdfdb82a4e` |
| Metric contract | `58a7b2f1389be691ef979f4fc4710f11d29027617c93f5a4eab580f73f3a6402` |
| Encoder revision | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` |

**Exact input order.** AAPL `0000320193-25-000079` Item 1 `68e0a75303e8c595642a7bbae95f025aac556ada3d1b6b10b0b29fb745cb99ec`; MSFT `0000950170-25-100235` Item 1 `4ac1db966b4ea0de05cc3916b20bac2c6616002804540e392338cf719ebb1da6`; AMZN `0001018724-25-000004` Item 1 `121f153d97d010b2036acd3a001173c7c8a5426c1582bb98560f6cce8109f21b`; GOOGL `0001652044-25-000014` Item 1 `7e2adaf0c7f14e5c7d28b8d4597fd268398324994569fe9e196582d0665fb69b`; META `0001326801-25-000017` Item 1 `647a4c678beb67ccce0ffdac9dde429f3c5be336281fd58a5bb972d624b74ede`.

**Execution counts.** Planned/completed logical document-length jobs: 25/25; planned/completed chunk vectors: 1083/1083; planned/completed company vectors: 50/50; actual batch call attempts: 145. The two pooling arms reuse each document-length job's chunk vectors.

## Complete arm inventory and length contrasts

Each row is one executed arm. Length contrasts use the 128-token arm with the same pooling rule. The reference rows have no length-change statistic.

| Body tokens | Pooling | Jobs | Chunks | Company vectors | Mean rank-2/3 gap | Mean vector distance | Mean signed pair change | Mean / max abs pair change | Pair-rank Spearman | Mean Top-2 overlap | Mean abs tracked-rank shift | Mean rank-2/3 gap change | Exact cutoff ties |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 118 | `equal_chunks` | 5 | 234 | 5 | 0.0298633623 | 0.00403765517 | 0.00159356639 | 0.00905742398 / 0.0182777736 | 0.939393939 | 1 | 0.2 | 0.000937988569 | 0 |
| 118 | `body_token_weighted` | 5 | 234 | 5 | 0.0321588417 | 0.00397103314 | 0.00313020309 | 0.00797332184 / 0.0184015506 | 0.951515152 | 1 | 0.2 | 0.00198414603 | 0 |
| 123 | `equal_chunks` | 5 | 225 | 5 | 0.0334025529 | 0.00389215948 | 0.00149212163 | 0.0121529131 / 0.0224411117 | 0.854545455 | 0.9 | 0.3 | 0.00447717914 | 0 |
| 123 | `body_token_weighted` | 5 | 225 | 5 | 0.0354551806 | 0.00377874538 | 0.00258013973 | 0.0120466934 / 0.0222347222 | 0.878787879 | 0.9 | 0.3 | 0.00528048489 | 0 |
| 128 | `equal_chunks` | 5 | 216 | 5 | 0.0289253737 | — | — | — | — | — | — | — | 0 |
| 128 | `body_token_weighted` | 5 | 216 | 5 | 0.0301746957 | — | — | — | — | — | — | — | 0 |
| 133 | `equal_chunks` | 5 | 207 | 5 | 0.0326618311 | 0.00442901258 | -0.00419672826 | 0.00927975315 / 0.017975636 | 0.866666667 | 0.9 | 0.4 | 0.00373645742 | 0 |
| 133 | `body_token_weighted` | 5 | 207 | 5 | 0.0337421562 | 0.00393568578 | -0.00163334368 | 0.00691559625 / 0.0146233773 | 0.951515152 | 1 | 0.1 | 0.00356746051 | 0 |
| 138 | `equal_chunks` | 5 | 201 | 5 | 0.0364034562 | 0.0048795072 | -0.00416193607 | 0.0094741234 / 0.020574848 | 0.890909091 | 0.9 | 0.3 | 0.0074780825 | 0 |
| 138 | `body_token_weighted` | 5 | 201 | 5 | 0.0358910727 | 0.00447921791 | -0.00116002587 | 0.00758092277 / 0.0218862348 | 0.975757576 | 1 | 0.1 | 0.005716377 | 0 |

Vector distance is `1 - cosine(reference firm vector, candidate firm vector)`. Signed pair change is candidate cosine minus reference cosine; absolute pair changes and Spearman use the ten dependent upper-triangle pairs. Top-2 overlap is intersection size divided by two, averaged across five firms. Tracked-rank shift follows the reference rank-2 and rank-3 peers, with ten firm-peer observations. The gap is rank-2 cosine minus rank-3 cosine. Exact cosine ties use ascending company ID; Spearman uses average ranks for exact ties. A constant upper triangle yields an undefined, unfilled Spearman value, with its reason in `metrics.json`.

## Top-2 set changes against same-pooling 128

The table includes every firm and nonreference arm whose Top-2 peer **set** differs from its same-pooling 128-token reference. Peer order and the rank-2/rank-3 boundary are shown separately; a zero gap is an exact tie.

| Body tokens | Pooling | Firm | Reference Top-2 | Candidate Top-2 | Reference rank 2 / rank 3 (gap) | Candidate rank 2 / rank 3 (gap) |
| ---: | --- | --- | --- | --- | --- | --- |
| 123 | `equal_chunks` | MSFT | GOOGL, AMZN | GOOGL, AAPL | AMZN / META (0.0162024094) | AAPL / META (0.000731044742) |
| 123 | `body_token_weighted` | MSFT | GOOGL, AMZN | GOOGL, AAPL | AMZN / META (0.0154353691) | AAPL / META (0.00268249745) |
| 133 | `equal_chunks` | MSFT | GOOGL, AMZN | GOOGL, AAPL | AMZN / META (0.0162024094) | AAPL / AMZN (0.00313265466) |
| 138 | `equal_chunks` | MSFT | GOOGL, AMZN | GOOGL, META | AMZN / META (0.0162024094) | META / AMZN (0.00355984351) |

Observed set-change rows by firm: AAPL 0, MSFT 4, AMZN 0, GOOGL 0, META 0; total 4. Each row is one firm/arm contrast, not an independent research sample.

The largest observed count is 4 for MSFT out of 4 set-change rows. This within-grid concentration does not establish firm-specific susceptibility.

## Tail-token preflight matrix

Each cell gives the final chunk's body-token count and its share of the complete Item 1 body-token count. Values are from the tokenizer-only preflight receipt, not model output.

| Firm | Total body tokens | 118 tail | 123 tail | 128 tail | 133 tail | 138 tail |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| AAPL | 2916 | 84 (2.88065844%) | 87 (2.98353909%) | 100 (3.42935528%) | 123 (4.218107%) | 18 (0.617283951%) |
| MSFT | 8489 | 111 (1.30757451%) | 2 (0.023559901%) | 41 (0.482977971%) | 110 (1.29579456%) | 71 (0.836376487%) |
| AMZN | 2552 | 74 (2.89968652%) | 92 (3.60501567%) | 120 (4.70219436%) | 25 (0.979623824%) | 68 (2.6645768%) |
| GOOGL | 5800 | 18 (0.310344828%) | 19 (0.327586207%) | 40 (0.689655172%) | 81 (1.39655172%) | 4 (0.0689655172%) |
| META | 7577 | 25 (0.329945889%) | 74 (0.976639831%) | 25 (0.329945889%) | 129 (1.70252079%) | 125 (1.64972944%) |

For the final chunk, equal-chunk pooling assigns weight `1 / chunk_count`; body-token-weighted pooling assigns `tail_tokens / total_body_tokens`. The change counts and tail shares describe this fixed input grid. Changing chunk length also changes internal boundaries and sometimes chunk count, so these rows do not isolate a tail-token or pooling cause.

## Same-length pooling contrasts

Weighted pooling is compared with equal-chunk pooling at each fixed length.

| Body tokens | Mean vector distance | Mean signed pair change | Mean / max abs pair change | Pair-rank Spearman | Mean Top-2 overlap | Mean abs tracked-rank shift | Mean rank-2/3 gap change |
| ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 118 | 0.000215396518 | 0.000204957687 | 0.00156106743 / 0.00426676104 | 1 | 1 | 0 | 0.00229547943 |
| 123 | 0.000206785422 | -0.000243660914 | 0.0011505113 / 0.00335577024 | 1 | 1 | 0 | 0.00205262771 |
| 128 | 0.000157933394 | -0.00133167901 | 0.00198562669 / 0.00448660265 | 0.987878788 | 1 | 0 | 0.00124932197 |
| 133 | 0.000387417045 | 0.00123170557 | 0.00226104428 / 0.00707534738 | 0.951515152 | 0.9 | 0.4 | 0.00108032506 |
| 138 | 0.000603607155 | 0.0016702312 | 0.00231519349 / 0.00544169648 | 0.963636364 | 0.9 | 0.2 | -0.000512383536 |

## Signed length-by-pooling interaction

For each nonreference length and pair, interaction equals the weighted-pooling signed cosine change from 128 minus the equal-pooling signed cosine change. This is a descriptive difference of contrasts, not a causal estimate.

| Body tokens | Mean signed interaction | Minimum / maximum pair interaction | Dependent pair count |
| ---: | ---: | --- | ---: |
| 118 | 0.00153663669 | -0.00108060759 / 0.00481776575 | 10 |
| 123 | 0.00108801809 | -0.000868897296 / 0.00297800004 | 10 |
| 133 | 0.00256338458 | -0.00125247782 / 0.00850853978 | 10 |
| 138 | 0.0030019102 | -0.00196273395 / 0.00767980317 | 10 |

## Historical M6 128-token compatibility readback

M6 dense receipt SHA-256: `38d04f96c6452bc367d866b26dcd325b2674c2212bb385aee02b87efec888360`. The comparison requires the same ordered accession/text hashes, pinned model files and historical config hash. Differences below are observed readback values, not a scientific acceptance threshold or a reproduction of another member's small-token observation.

| 128-token pooling | Max abs company-vector difference | Max abs cosine-matrix difference | Historical vector SHA-256 |
| --- | ---: | ---: | --- |
| `equal_chunks` | 0 | 0 | `e1fd53fee1169750c6306a043673bab6d618663f51b7e2feb7b57b207cee03b4` |
| `body_token_weighted` | 0 | 0 | `39ecb3596442f3aae93ce932a062b4e7754827bc7ae000e6e33739cefa92aefd` |

## Reading the complete machine result

`metrics.json` retains each arm's complete cosine matrix, ordered peer list, Top-2 set and rank-2/3 gap; every firm's vector distance, Top-2 overlap, margin change and tracked rank movement; every pair's signed and absolute cosine change; each undefined Spearman reason; and every signed interaction. The tables above summarize every declared arm and metric family without discarding the underlying company and pair records.

No p-value, population claim, semantic improvement, market forecast, trading loss or net economic benefit follows from this five-company, one-model probe. M5 remains unfrozen.
