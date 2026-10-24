# Roadmap

| Milestone | Scope | Current disposition |
|---|---|---|
| M0 | Runnable minimal package, config, logging, manifest, offline doctor | Implement and verify in this round |
| M1 | Discover real 10-Ks, preserve source and metadata, extract Item 1 with checks | Five real filings ingested and revalidated from saved raw bytes in this demonstration |
| M2 | Validate M1 corpus, pin real pretrained model/tokenizer, map chunks and create company embeddings | Five real Item 1 documents embedded with checked input/output hashes |
| M3 | Same-config repeat, 128/192 chunk-length stability, orthogonal invariance check | Descriptive checks completed on the same five companies; see run report |
| M4 | Massive account/identity/price audit, past-window returns and PCA; same-corpus TF-IDF control | Existing Default key used for one process; five real identity and daily-price series, past-window returns and PCA passed a separately coded read-only self-audit |
| M5 | Freeze formal task, sample, models, time split, controls and metrics with researcher/professor | Corrected five-company historical filing chronology, separate fixed-text stability and optional financial-extension gates, decision sheet and professor agenda prepared; Overleaf compile errors repaired. Exact first-public/text-vintage evidence remains open for financial extensions; no formal experiment run |
| Later | Out-of-sample incremental text-versus-price evaluation and point-in-time market audit | Not attempted |

## M6 route update (2026-10-24)

M6 adds [BM25 lexical controls and long-filing length sensitivity](docs/M6_METHOD.md) as engineering work alongside the unfrozen M5 protocol. Implemented: directed BM25 scoring, separately named BM25-term-weight cosine, b=0/no-length control, optional body-token-weighted dense pooling, and a reviewed-section adapter for original 10-K/10-Q. M5 scientific choices are not bypassed. Real-data M6 evaluation requires the omitted raw filing/text corpus and locally pinned model files; it must not be replaced by synthetic company findings.

Read the [plan-to-evidence contract](docs/M6_PLAN_EVIDENCE.md) and [M6 acceptance report](reports/2026-10-24_M6_engineering.md) before reporting completion. Neither BM25 nor vector normalization is assumed to improve results without evaluation.

No claim about model performance, trading returns or text's financial incremental value follows from this engineering demonstration.

