# Descriptive results from saved runs

All numbers here are copied from or rendered from the saved, hash-checked M1–M4 outputs identified in [`PROVENANCE.json`](PROVENANCE.json). Figures are visualizations of those saved numbers, not new experiments. Company order is AAPL, MSFT, AMZN, GOOGL, META.

| Existing check | Saved result | Scope |
|---|---:|---|
| Same-configuration repeat: maximum vector and cosine difference | 0 and 0 | Fixed pretrained weights; not independent training seeds |
| 128 versus 192 body-token chunks: off-diagonal upper-triangle Spearman | 0.9151515151515152 | 10 dependent company pairs; no significance test |
| Top-2 neighbor overlap / 2 | AAPL 1.0; MSFT 0.5; AMZN 1.0; GOOGL 1.0; META 1.0 | Exact cosine ties broken by ascending ticker |
| Orthogonal Householder check: max Gram/cosine difference | 1.2212453270876722e-15 | Same vector dimension; no Procrustes alignment |
| TF-IDF fit boundary | 5 validated Item 1 documents; 2,902 terms | Same-corpus lexical control, no held-out evaluation |
| Price PCA | 250 bars/security; 249 complete return dates; 5×2 loadings; effective rank 5 | Centered, unscaled, split-adjusted close-to-close returns; `K=2` is a demo setting |

Files:

- [`embedding_cosine_128.csv`](embedding_cosine_128.csv), [`embedding_cosine_192.csv`](embedding_cosine_192.csv): five-company document-vector cosine matrices.
- [`stability_top2_overlap.csv`](stability_top2_overlap.csv), [`stability_metrics.json`](stability_metrics.json), and [overlap figure](../figures/stability_top2_overlap.svg).
- [`tfidf_cosine.csv`](tfidf_cosine.csv): five-document TF-IDF cosine matrix, with fit scope in provenance.
- [`pca_stock_loadings.csv`](pca_stock_loadings.csv), [`pca_variance_fraction.csv`](pca_variance_fraction.csv), and [variance figure](../figures/pca_variance_fraction.svg). Variance fractions are computed as each saved singular value squared divided by the sum of all five saved singular values squared; they are a presentation of the existing PCA fit.

The price PCA and TF-IDF results are **separate engineering baselines**. No price-versus-text prediction or incremental-value evaluation was run. The split-adjusted price changes exclude dividends and are not total returns. Exact first-public filing timestamps, decision-time text vintages and formal M5 parameters remain unresolved.
