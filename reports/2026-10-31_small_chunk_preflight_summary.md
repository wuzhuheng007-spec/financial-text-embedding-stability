# Small-chunk tokenization preflight summary

Status: **PASS_TOKENIZATION_ONLY**. Candidate: **PROPOSED**, researcher accepted: **false**.
Encoder, generation, retrieval evaluation and financial experiments: **NOT_RUN**.

Source: ignored local `artifacts/round2/20261031-small-chunk-tokenization-preflight-v3/receipt.json`, SHA-256 `f0710267080e04c82f8e4685db61c2a6a82f271b304e6978ba5e7fe4c79e3d72`. The candidate config, M1 index, local model/tokenizer files, historical 128-token map and code files are hash-bound in that receipt; no filing text or model vector is reproduced here.

| Company | Accession | Item 1 SHA-256 | Body tokens | Chunks at 118 / 123 / 128 / 133 / 138 |
| --- | --- | --- | ---: | --- |
| AAPL | `0000320193-25-000079` | `68e0a75303e8c595642a7bbae95f025aac556ada3d1b6b10b0b29fb745cb99ec` | 2916 | 25 / 24 / 23 / 22 / 22 |
| MSFT | `0000950170-25-100235` | `4ac1db966b4ea0de05cc3916b20bac2c6616002804540e392338cf719ebb1da6` | 8489 | 72 / 70 / 67 / 64 / 62 |
| AMZN | `0001018724-25-000004` | `121f153d97d010b2036acd3a001173c7c8a5426c1582bb98560f6cce8109f21b` | 2552 | 22 / 21 / 20 / 20 / 19 |
| GOOGL | `0001652044-25-000014` | `7e2adaf0c7f14e5c7d28b8d4597fd268398324994569fe9e196582d0665fb69b` | 5800 | 50 / 48 / 46 / 44 / 43 |
| META | `0001326801-25-000017` | `647a4c678beb67ccce0ffdac9dde429f3c5be336281fd58a5bb972d624b74ede` | 7577 | 65 / 62 / 60 / 57 / 55 |

Historical 128-token map alignment: **PASS**.

The largest planned model input is 140 tokens including two special tokens, below the pinned model's 256-token limit. All 25 company-by-length tokenizations have complete, nonoverlapping body-token coverage and positive pooling weights.

Future logical document-length encoding jobs: 25; future chunk vectors: 1083; pooling outputs: 50. These are planned counts, not executions.

The preflight checks tokenization and source identity only. No small-step model result or effect size exists.
