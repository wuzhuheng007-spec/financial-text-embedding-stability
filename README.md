# Financial Text Embedding Stability

This repository is a curated engineering share of a research project on the stability of financial-text representations. It also prepares, but has **not run**, a formal test of whether text adds value beyond price-only baselines while avoiding look-ahead bias. The present evidence is a small, descriptive demonstration; no financial incremental value or historical tradability has been established.

## What has been completed

- Five saved SEC 10-K Item 1 Business extracts were bound to original filing identities and hashes. One pinned pretrained encoder (`sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`) supported 128/192-body-token, nonoverlapping chunk comparisons and a same-setting repeat check. A fixed-weight repeat is not an independent training seed.
- Five-document TF-IDF, BM25 (`k1=1.2`, `b=0, 0.75, 1`), equal versus body-token-weighted aggregation, and tail-chunk diagnostics were run on the saved Item 1 texts. Directed BM25 query-to-document scores and BM25 term-weight cosine are distinct measures. The [M6](results/m6_real5_20261025/RESULTS_SCOPE.md) and [M7](results/m7_exploratory_20261025/RESULTS_SCOPE.md) tables and figures are descriptive for this exact corpus and model.
- A price-only engineering demonstration used 250 saved 2025 split-adjusted daily bars per company, 249 complete close-to-close return dates, and centered, unscaled PCA with demo `K=2`. Split adjustment does not include dividends. `K=2` has no recorded scientific selection rationale. The price window has no approved formal role as trailing features, labels, or static comparison input.
- Four additional 10-Q MD&A filings received source and extraction self-checks. Review state remains company- and version-specific. AMZN and GOOGL accepted structured derivatives passed a separate project provenance gate **with limitations**; AAPL and MSFT lack company-level sign-off, and META remains pending. GOOGL MDA-1/MDA-5 header-to-value mappings remain unresolved.
- A local LlamaIndex-based **retrieval-only** assistant indexed 166 source-linked chunks from the exact AMZN and GOOGL derivatives. In a saved, agent-authored development check, BM25, dense, and hybrid retrieval each returned the complete preselected source span within the top five for 5/5 eligible questions. Those five questions use four unique source anchors. The measure is neither recall@5 nor answer accuracy. The sixth probe returned evidence with `answer=null`; it did not verify semantic abstention. Generator and genuine neural MoE inference were **not run**.

The project has not run a formal financial experiment, independent human retrieval evaluation, or generator evaluation. Same-agent source checks are self-checks, not independent audits. M5 scientific parameters remain **unfrozen**.

## Reading order

1. [Evidence trace, M0–M7](docs/EVIDENCE_TRACE_M0_M7.md) and [M8–M10](docs/EVIDENCE_TRACE_M8_M10.md): claim, saved result field, run identity, input/model version, and limit.
2. [Current-status addendum](results/CURRENT_STATUS_ADDENDUM.md), [M6 descriptive outputs](results/m6_real5_20261025/RESULTS_SCOPE.md), [M7 exploratory outputs](results/m7_exploratory_20261025/RESULTS_SCOPE.md), and [public M10 summary](results/m10_assistant_demo_20261028/RESULTS_SCOPE.md). The M6/M7 scope files are preserved historical snapshots.
3. [M10 assistant method](docs/M10_RAG_ASSISTANT.md), [M5 protocol draft](docs/M5_PROTOCOL_DRAFT.md), the [proposed technical roadmap](docs/PROPOSED_TECHNICAL_ROADMAP.md), and its [ten-image source map](docs/ROUTE_IMAGE_SOURCE_MAP.md).
4. [Publication scope and exclusions](SHARING_SCOPE.md) and [release audit](docs/PUBLICATION_AUDIT_2026_10_28.md).

## Install and reproduce

Use Python 3.11+ from a fresh clone. The offline doctor and software tests do not fetch SEC filings or models:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[embed,rag,test]"
.\.venv\Scripts\python -m finembed doctor --config configs/smoke.yaml --offline
.\.venv\Scripts\python -m pytest -q
```

The `rag` extra pins the tested `llama-index-core==0.14.25`. The [M10 method](docs/M10_RAG_ASSISTANT.md) gives commands for a new local index and retrieval check **only after** the exact hash-bound accepted text and pinned encoder files have been obtained separately. A clone of this public repository alone cannot reproduce private-source outputs byte-for-byte. New retrieval checks require new run directories and source receipts; do not relabel old results as a rerun.

For SEC ingestion, set a real name/organization and contact email in the process environment as `SEC_USER_AGENT`; see [the SEC EDGAR API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces). For the price-only workflow, obtain your own historical Stocks REST entitlement and set `MASSIVE_API_KEY` in the process environment. No key or `.env` file is included. The original SEC responses, vendor price responses, model weights, accepted derivatives, private review messages, and vector index are excluded from this share. Source IDs, hashes, provenance, and procedures remain in the evidence trace. Do not substitute synthetic fixtures for real filings or model inference.

## Next decisions and limits

The [roadmap](docs/PROPOSED_TECHNICAL_ROADMAP.md) proposes completing exact-version 10-Q review, building a researcher-checked retrieval set, and broadening representation-stability analysis. Generation and a genuine MoE are optional, separate engineering gates. Historical prediction or risk estimation requires a researcher-approved M5 protocol with explicit public-availability evidence, temporal splits, fit boundaries, baselines, and metrics. None of those proposed scientific choices is frozen here.

**Overleaf:** the existing team project link and online update remain pending live project verification; this README does not imply GitHub and Overleaf are synchronized.

**License:** no repository license is granted or implied. Redistribution and licensing of team code are pending confirmation. Third-party source, model, and dependency terms remain with their owners.
