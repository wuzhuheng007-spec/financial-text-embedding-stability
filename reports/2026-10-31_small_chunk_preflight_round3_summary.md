# Round 3 small-chunk tokenization preflight: shareable summary

**Result:** `PASS_TOKENIZATION_ONLY`. Candidate `PROPOSED` and unchanged;
researcher decision `MISSING`; five-company candidate inference `NOT_RUN`.
This summary reports no new company vectors, similarity effects, retrieval
scores or financial outcomes.

The ignored local machine receipt is
`artifacts/round3/20261031-small-chunk-tokenization-preflight-v1/receipt.json`,
SHA-256 `db3b7ae5dbbf735a143f5ae3a179e808444caf70bfb1147f132cbaeb44182481`.
It was created after source commit
`39c13bcdbee05634fbc8248ffd715615cfab3fa9` and binds 65 files,
including the unchanged candidate SHA-256
`a307536980057c227d63fafb11b7b25b4615a2923455717616a7431f05d82388`,
the original M1 corpus index, four local model/tokenizer files, the historical
128-token map, the metric contract, and the current source files. The local
`verify` command returned `PASS_BOUND_FILES_UNCHANGED`. The machine receipt
contains local private paths and is intentionally not committed or copied here.

| Company | 10-K accession | Exact Item 1 SHA-256 | Body tokens | Chunk counts at 118 / 123 / 128 / 133 / 138 |
| --- | --- | --- | ---: | --- |
| AAPL | `0000320193-25-000079` | `68e0a75303e8c595642a7bbae95f025aac556ada3d1b6b10b0b29fb745cb99ec` | 2,916 | 25 / 24 / 23 / 22 / 22 |
| MSFT | `0000950170-25-100235` | `4ac1db966b4ea0de05cc3916b20bac2c6616002804540e392338cf719ebb1da6` | 8,489 | 72 / 70 / 67 / 64 / 62 |
| AMZN | `0001018724-25-000004` | `121f153d97d010b2036acd3a001173c7c8a5426c1582bb98560f6cce8109f21b` | 2,552 | 22 / 21 / 20 / 20 / 19 |
| GOOGL | `0001652044-25-000014` | `7e2adaf0c7f14e5c7d28b8d4597fd268398324994569fe9e196582d0665fb69b` | 5,800 | 50 / 48 / 46 / 44 / 43 |
| META | `0001326801-25-000017` | `647a4c678beb67ccce0ffdac9dde429f3c5be336281fd58a5bb972d624b74ede` | 7,577 | 65 / 62 / 60 / 57 / 55 |

All 25 company-by-length tokenizations passed exact, nonoverlapping body-token
coverage, positive pooling weights and special-token budget checks. The
maximum proposed model input was 140 tokens including two specials, below the
local 256-token limit. The historical 128-token map matched all 216 rows.
The receipt records per-length chunk-map hashes and the current import and
dependency identity. It recorded zero Encoder sessions and zero forward calls
**within this tokenizer-only preflight**.

Future **planned** counts are 25 company-length jobs, 1,083 chunk vectors and
50 pooled company vectors. Actual executions of this candidate remain zero.
These planned counts are not batch-call counts or model results. An earlier
software regression command in this round inadvertently performed real
inference on synthetic text; see the [round 3 handoff](2026-10-31_research_plan_round3_handoff.md)
for that distinct boundary deviation. The old v3 receipt is preserved but is
not valid for the new code inventory and source-root/semantic checks.
