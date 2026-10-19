# M2–M3 engineering demonstration method

## Inputs and fixed encoder

The smoke list contains AAPL, MSFT, AMZN, GOOGL and META. It is an engineering demonstration sample. Each included document must pass the M1 raw-byte-to-Item-1 audit; exclusions remain visible. The exact model and tokenizer revision is `sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The ONNX model, tokenizer and configuration SHA-256 values are in `configs/smoke.yaml`; `model-fetch` verifies them. The model card describes a 384-dimensional short-text encoder and attention-mask mean pooling. Its model-specific limit is 256 tokens; the transformer architecture has 512 positions. This demonstration respects the lower limit. The model card notes fine-tuning at 128 tokens, so the 192-token arm probes a length change beyond that training sequence length, without claiming a general result. [Pinned model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md).

The published `tokenizer.json` has default truncation and padding at 128 tokens. Both are disabled for whole-document tokenization. Each resulting WordPiece ID has a source character offset. Body IDs are partitioned into contiguous nonoverlapping chunks of 128 tokens (baseline) or 192 tokens (one-factor alternative). The `[CLS]` and `[SEP]` IDs are then added; all actual inputs are checked against the 256-token model limit. `chunk_map.jsonl` saves document identity, token and character ranges, source-span hash, token IDs and final input length. There is no overlap or silent truncation.

The ONNX last hidden state is pooled with the attention mask, including special tokens, as in the model card. Per configuration, chunk vectors are L2-normalized, averaged with equal weights, then the document vector is L2-normalized. The configuration flags are written in each arm's `arm_config.json`. CPU execution is used. All model files, input files and outputs are hashed in the run manifest.

## Checks and statistics

The 128-token arm is run twice with the same files, model, provider and aggregation. The maximum absolute vector and cosine differences must be at most the configured tolerance before the 192-token arm is interpreted. This is repeat inference with fixed pretrained weights, **not** independent training seeds.

The 192-token arm changes only the body tokens per chunk. The same validated companies in the same row order are used. Cosine matrices exclude the diagonal and use only `i<j` entries for Spearman. Exactly equal float64 cosine values receive average ranks; rank correlation is undefined if there are fewer than three pairs or constant ranks. Nonfinite embeddings/cosines fail without imputation. Top-k excludes self, sorts by descending cosine with exact ties broken by ascending ticker, and reports per-company set intersection divided by k. Invalid results are `null` with a reason, never silently converted to zero.

A fixed Householder orthogonal reflection transforms the baseline company vectors in the same dimension. The check compares `QᵀQ`, Gram and cosine differences to explicit numerical tolerances. No cross-model or different-dimension Procrustes alignment is performed.

This is a small-sample descriptive demonstration. Company-pair similarities are dependent; no pairwise significance test, financial incremental-value claim or return conclusion is computed. SEC acceptance timestamps and the present-day ticker map do not settle point-in-time availability; formal choices remain in `PROTOCOL.md`.
