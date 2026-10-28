# M6 saved-output self-check

Run: `20261024T221032Z-real5`. Same five real saved 10-K Item 1 Business documents: AAPL, MSFT, AMZN, GOOGL, META. All saved source and output hashes, company order, fixed model identity and exact repeated inference passed the checks in `audit.json`.

Dense model: `sentence-transformers/all-MiniLM-L6-v2` at fixed revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The tokenizer uses the same recorded revision.

This is a same-agent self-check of a small descriptive engineering demonstration. The fixed-weight repeat is not a separate training seed. No financial experiment, return prediction, incremental-value test, independent audit, or historical availability conclusion was performed.

## Reading order

`audit.json` → `tfidf_same_tokenizer_fit.json` → `length_table.csv` → `method_table.csv` → `comparison_metrics.json` → `neighbors.csv` and `top2_overlap.csv` → SVG figures.

M6 TF-IDF was refit on the same five texts and `[a-z0-9]+` tokenizer as BM25. The verified M4 TF-IDF is retained as a historical reference only; its tokenizer was `[a-z][a-z0-9]+`.

BM25 directed query-document scores are in `directed_bm25_neighbors.csv`. They are not used as symmetric cosine or covariance matrices.

## Selected descriptive comparisons

| Reference | Compared | Upper-triangle Spearman | Mean top-2 overlap | Max absolute off-diagonal difference |
|---|---|---:|---:|---:|
| TF-IDF M6 same-tokenizer | TF-IDF M4 historical | 1.0000 | 1.000 | 0.00833 |
| TF-IDF M6 same-tokenizer | BM25 term-weight cosine b0 | 0.8061 | 1.000 | 0.71574 |
| TF-IDF M6 same-tokenizer | BM25 term-weight cosine b0.75 | 0.8061 | 1.000 | 0.71627 |
| TF-IDF M6 same-tokenizer | BM25 term-weight cosine b1 | 0.8061 | 1.000 | 0.71663 |
| BM25 term-weight cosine b0 | BM25 term-weight cosine b0.75 | 1.0000 | 1.000 | 0.00145 |
| BM25 term-weight cosine b0 | BM25 term-weight cosine b1 | 1.0000 | 1.000 | 0.00190 |
| Dense 128 equal_chunks | Dense 128 body_token_weighted | 0.9879 | 1.000 | 0.00449 |
| Dense 192 equal_chunks | Dense 192 body_token_weighted | 0.9879 | 0.900 | 0.00724 |
| Dense 128 equal_chunks | Dense 192 equal_chunks | 0.9152 | 0.900 | 0.02903 |
| Dense 128 body_token_weighted | Dense 192 body_token_weighted | 0.9758 | 1.000 | 0.02956 |

Metrics use ten dependent company pairs, with no p-values. Changed similarity does not imply improvement; stable similarity does not establish financial validity or rule out forward-looking bias.
