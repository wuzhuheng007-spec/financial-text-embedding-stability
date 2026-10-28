# M7 local derived-result subset

These files are copied or recomputed from the ignored local run `artifacts/m7/20261024T223714Z-tail-bm25-10q/`. They are descriptive engineering outputs, not formal model evaluation or public-release approval. Source Item 1/MD&A text, SEC responses, caches, chunk embeddings and model weights stay outside this directory. Human review of the 10-Q boundary is pending.

| File | Source | What it contains |
|---|---|---|
| `tail_weight_diagnostics.csv` | `tail_v2/tail_weight_diagnostics.csv` | Five Item 1 companies × two chunk sizes × four named aggregation/ablation rows, with source-span hashes but no filing text. Tail-only and token-weighted rows are algebraically equivalent here. |
| `tail_weight_effects.svg` | `tail_v2/tail_weight_effects.svg` | Five-company vector distance from equal pooling; exploratory, not a quality measure. |
| `bm25_normalization_diagnostics.csv` | `bm25/bm25_normalization_diagnostics.csv` | Five companies × three b settings, lexical frequencies, vector norms, scalar-fit residuals and normalized direction changes. |
| `bm25_peer_rankings.csv` | `bm25/bm25_peer_rankings.csv` | Separately identified directed BM25 and symmetric term-weight cosine peer orders. |
| `bm25_scaling_residuals.svg` | `bm25/bm25_scaling_residuals.svg` | Same-support best-scalar fit residuals before L2; this is not relevance or financial performance. |
| `tenq_pilot_coverage.csv` | `tenq/representation_v2/representation_checks.json` | Two AAPL MD&A chunk coverage rows and single-document aggregation cosines only; no cross-company similarity. |

The frozen M1 index SHA-256 is `ce54c348458e2c90abee0435f72fe84ff3895e7695c2a2f4ed212eb61035a18c`; source configuration SHA-256 is `d05d039580b47f9a7ed3aec1e88982a8de16a6af1df23686e8a93308203e7869`. The model/tokenizer revision is `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. Full input/output receipts, calculations, failures and tests remain in the ignored local run. The source report is [`reports/2026-10-25_M7_mechanism_and_10q.md`](../../reports/2026-10-25_M7_mechanism_and_10q.md).
