# M6 five-company 10-K engineering run — 2026-10-25

**Status:** local descriptive run and same-agent self-check passed. This is not a formal financial experiment or an independent audit. The M5 scientific protocol remains unfrozen.

## Frozen inputs and code

- Original M1 five-company index: `<PROTECTED_ORIGINAL_CHECKOUT>\artifacts\runs\20261019T003627Z-d3043c3e\corpus_index.json`, SHA-256 `ce54c348458e2c90abee0435f72fe84ff3895e7695c2a2f4ed212eb61035a18c`. Included order: AAPL, MSFT, AMZN, GOOGL, META. The saved raw SEC submissions, primary text, Item 1 text, metadata and boundaries were revalidated locally; five of five passed. No SEC text was redownloaded or replaced.
- Original configuration: `<PROTECTED_ORIGINAL_CHECKOUT>\configs\smoke.yaml`, SHA-256 `d05d039580b47f9a7ed3aec1e88982a8de16a6af1df23686e8a93308203e7869`.
- Fixed encoder and tokenizer: `sentence-transformers/all-MiniLM-L6-v2` revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The local ONNX, tokenizer and two configuration files passed the pinned hashes in `pre_run_input_audit.json`. No weights were copied into this checkout.
- Historical M4 TF-IDF: its `fit_boundary.json` SHA-256 is `8aec2bfd666a7901d602733c0b341c23755bb37141c178a436aa4fcc0d889919`; its `[a-z][a-z0-9]+` token pattern differs from the M6 BM25 pattern. A new M6 TF-IDF was fitted on the **same five texts and `[a-z0-9]+` tokenizer** as BM25. The historical M4 result is labeled as a separate reference.
- ZIP reviewed before integration: `E:\M6_bm25_length_controls_2026-10-24.zip`, SHA-256 `3ca29526925e7243695069ccd5bed445e235fb1d6d959a17759370a862080d2a`; all 58 internal checksum entries matched. The isolated checkout started from `28265b7851c2187e297b3aacf91fa3fa505c02f4` on local branch `codex/m6-real-corpus-20261025`. The original `quant` files, Git status and old results were left in place.

After the run, the five official SEC filing-detail pages were reopened to check accession, 10-K form, filer/CIK and report period: [AAPL](https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.html), [MSFT](https://www.sec.gov/Archives/edgar/data/789019/000095017025100235/0000950170-25-100235-index.html), [AMZN](https://www.sec.gov/Archives/edgar/data/1018724/000101872425000004/0001018724-25-000004-index.html), [GOOGL](https://www.sec.gov/Archives/edgar/data/1652044/000165204425000014/0001652044-25-000014-index.html), and [META](https://www.sec.gov/Archives/edgar/data/1326801/000132680125000017/0001326801-25-000017-index.html). The full SEC text URLs exceeded the web reader's size limit, so this online check does not revalidate the saved raw byte hashes. Local raw bytes and M1 provenance provide that separate check; first-public time remains unverified.

## Execution and checks

Run group: `artifacts/m6/20261024T221032Z-real5/` in this checkout. The full, hash-indexed raw outputs remain local under `lexical/`, `dense/`, and `dense_repeat/`. `report_v3/` is the authoritative derived report; earlier `report/` and `report_v2/` are retained revisions of figure labels, both marked as superseded in the command record. The exact commands, source hashes, input/output list and test logs are in the run group.

The final test command `python -m pytest -q -r s` yielded **47 passed, 2 skipped**. The two tests require model files inside this curated checkout, where weights are deliberately absent; the real inference used the separately verified pinned files in the original private project. The first pre-run test log showed 44 passed, 2 skipped before the three reporter tests were added. `git diff --check` passed.

Same-configuration repeated inference passed **four of four** dense arms (128/192 body tokens × equal/body-token-weighted pooling): maximum vector and cosine absolute difference **0**, within the original `1e-5` tolerance. The new equal-pooling vectors and cosine matrices also matched the original M2 128/192 outputs exactly. This is fixed-weight repeat inference, not independently trained seeds. The run saved per-chunk token and source-character offsets, body-token weights, source-span hashes, special-token-inclusive input lengths, and coverage checks. All five documents had PASS coverage at both lengths; the 256-token model input limit was respected.

The reporter read saved M1/M4/M6 inputs, rehashed raw SEC and Item 1 bytes plus saved outputs, recomputed cosine from saved vectors, ranks, overlaps and matrix differences with separately written arithmetic, and checked the three SVGs against the same tables. This was **same-agent self-check**; it did not independently recollect SEC filings or rehash the 90 MB model file itself. The pre-run model verification did the latter.

## Descriptive observations

All comparisons use the same five Item 1 Business documents, ten dependent off-diagonal company pairs, exact-value average Spearman ties, and ticker order for exact neighbor ties. Undefined statistics remain null with reasons. No company-pair p-values were computed.

| Comparison | Upper-triangle Spearman | Mean company Top-2 overlap | Max absolute off-diagonal difference |
|---|---:|---:|---:|
| BM25 term-weight cosine b=0 vs b=1 | 1.0000 | 1.000 | 0.00190 |
| Dense 128 equal vs body-token weighted | 0.9879 | 1.000 | 0.00449 |
| Dense 192 equal vs body-token weighted | 0.9879 | 0.900 | 0.00724 |
| Dense 128 vs 192, equal pooling | 0.9152 | 0.900 | 0.02903 |
| Dense 128 vs 192, body-token weighted | 0.9758 | 1.000 | 0.02956 |

BM25 used `k1=1.2` and `b={0,0.75,1}`. Its directed query-to-document scores are saved separately from symmetric **BM25 term-weight cosine**; the former is not treated as a similarity or covariance matrix. Chunk embeddings were L2 normalized, then aggregated with equal or actual body-token weights, then document L2 normalized. Length and tail-block changes can affect these descriptive outcomes. A changed matrix does not establish an improvement, and a stable matrix does not establish financial value.

The [saved-matrix sensitivity check](../results/m6_real5_20261025/sensitivity_diagnostics.json) found **zero exact rank-2/rank-3 ties across the four dense arms**. In the 192-token arms, MSFT's second neighbor changed from META under equal pooling to AMZN under body-token weighting; the saved rank-2/rank-3 gaps were 0.001190 and 0.003609, respectively. MSFT had 45 chunks and a 41-body-token tail at length 192, so that tail received `1/45` weight with equal pooling versus `41/8489` with body-token weighting. These observations identify a plausible sensitivity channel, not a causal explanation or relevance improvement.

## Local artifacts and sharing boundary

Start with `report_v3/report.md`, `report_v3/audit.json`, `report_v3/length_table.csv`, `report_v3/method_table.csv`, `report_v3/comparison_metrics.json`, `report_v3/neighbors.csv`, `report_v3/top2_overlap.csv`, and the three SVGs. The output receipts hash every saved M6 file; `report_v3/audit.json` hashes the report outputs. A small [curated local copy of the tables and figures](../results/m6_real5_20261025/RESULTS_SCOPE.md) carries its own SHA-256 list; its copied files match report_v3 byte for byte, and the separately computed tail/tie diagnostic is identified in the list. Original SEC text, raw Massive responses and model weights are absent from this curated checkout and are not included in a public upload.

The existing GitHub main and earlier M6 draft branch were checked; this new real-run branch and its results were **not pushed**. A separate [10-Q plan](../docs/M6_10Q_NEXT.md) has 13 TODO items and no executed 10-Q work. The next scientific decision remains the M5 protocol and historical-availability gate for optional financial extensions. This run does not test returns, risk estimation, causal effects, incremental financial value or the absence of look-ahead bias.
