# Financial Text Embedding Stability — Engineering Demo

This repository is a curated share copy of a five-company research engineering demonstration. The project asks how stable company representations from financial text are, whether text eventually adds value beyond price-only baselines, and how to test for look-ahead bias. **Only the engineering and descriptive demonstration is complete. No formal financial experiment has run.**

## What has been done

- Five real SEC Form 10-K original submissions were identified and their Item 1 Business sections extracted with source offsets and SHA-256 provenance. The original filings and extracted text are **not** redistributed here.
- One pinned pretrained encoder and tokenizer (`sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`) produced company embeddings. Fixed-weight repeat inference was checked; the only deliberate alternative changed nonoverlapping body chunks from 128 to 192 tokens. This is not an independent training-seed comparison.
- A five-document TF-IDF control was fitted only to the validated Item 1 corpus. A separate price-only engineering baseline used Massive split-adjusted daily bars from the configured 2025 window: 250 observed price dates, 249 complete close-to-close return dates, and a centered, unscaled PCA with demo `K=2`. The bars exclude dividends; `K=2` has no recorded scientific selection rationale.
- A same-agent, separately coded read-only self-audit checked saved M4 response hashes, security identity, returns and PCA relationships. It was **not an independent review**.

The tables and figures under [`results/`](results/RESULTS.md) are small-sample descriptive outputs. They do **not** show predictive performance, establish text's incremental financial value, or rule out future-information contamination. M5 formal parameters remain **unfrozen**.

## Reading order

1. [`results/RESULTS.md`](results/RESULTS.md) and the CSV/SVG outputs for the descriptive findings.
2. [`results/PROVENANCE.json`](results/PROVENANCE.json) and [`SHARING_SCOPE.md`](SHARING_SCOPE.md) for run IDs, source hashes and omissions.
3. [`docs/M2_M3_METHOD.md`](docs/M2_M3_METHOD.md), [`docs/M4_METHOD.md`](docs/M4_METHOD.md), and the [M4 closeout](reports/2026-10-19_M4_closeout.md) for implementation and audit limits.
4. The [corrected filing/price chronology](reports/2026-10-19_text_price_time_alignment.md), [M5 track gates](docs/M5_TRACKS.md), [protocol decision sheet](docs/M5_PROTOCOL_DRAFT.md), and [professor agenda](reports/2026-10-19_M5_professor_agenda.md).
5. [`reports/2026-10-19_overleaf_progress.tex`](reports/2026-10-19_overleaf_progress.tex) is the author-written progress fragment. Team Overleaf sources are not included.

## Install and reproduce the workflow

Python 3.11+ is required. From a fresh clone:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[embed,test]"
.\.venv\Scripts\python -m finembed doctor --config configs/smoke.yaml --offline
.\.venv\Scripts\python -m pytest -q
```

The share ZIP omits original filings, vendor responses and model weights. To rerun the data workflow, set your **own real** SEC contact identity in the process environment as `SEC_USER_AGENT` (name/organization plus contact email), then run:

```powershell
.\.venv\Scripts\python -m finembed ingest --config configs/smoke.yaml
.\.venv\Scripts\python -m finembed model-fetch --config configs/smoke.yaml
.\.venv\Scripts\python -m finembed analyze --config configs/smoke.yaml --corpus artifacts/runs/<M1-run-id>/corpus_index.json
```

For the price-only M4 workflow, obtain historical Stocks REST access from Massive, set the console-issued `MASSIVE_API_KEY` in the **running process environment**, and run:

```powershell
.\.venv\Scripts\python -m finembed m4 --config configs/smoke.yaml --m4-config configs/m4_demo.yaml --corpus artifacts/runs/<M1-run-id>/corpus_index.json
```

`.env.example` lists variable names only; the application does not auto-load `.env`. Do not commit credentials. SEC filings can be obtained through the [EDGAR data API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and archive URLs recorded in the [chronology JSON](reports/2026-10-19_text_price_time_alignment.json). Model files are fetched from the pinned [Hugging Face revision](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41) and checked against configuration hashes. Massive daily aggregates require your own authorization; see the [provider documentation](https://massive.com/docs/rest/stocks/aggregates/custom-bars). Provider revisions, entitlements or historical text changes may prevent byte-identical reruns; compare against `results/PROVENANCE.json` and retain new receipts.

## Limits and next step

### M6 engineering extension

See [M6 method and commands](docs/M6_METHOD.md), [plan/evidence](docs/M6_PLAN_EVIDENCE.md), [engineering acceptance](reports/2026-10-24_M6_engineering.md), and the [later publication review](reports/2026-10-25_M6_publication_review.md). `python -m finembed.m6` adds BM25 length-control comparisons and optional equal-chunk versus token-weighted dense pooling on your hash-checked local corpus. BM25 is a lexical ranking method; its directed scores are not dense embeddings or covariance. A reviewed 10-Q section adapter is available, but automated 10-Q discovery/extraction and real-data M6 results are not claimed. Original results are preserved. M5 remains unfrozen.

`MANIFEST_SHA256.json` is the retained original Windows share-export manifest, not a claim that M6 source still has the old hashes. Fourteen historical text files have CRLF bytes in that local export and LF bytes in Git; their content is otherwise identical. Use `M6_CHECKSUMS.json` for SHA-256 values of the committed Git blobs in this updated branch.

The five firms, one pretrained model and 2025 price window were engineering choices. The exact first-public filing instants and decision-time Item 1 vintages are not established here. The 2025 prices have no assigned role as trailing features, future labels or static comparison inputs in a formal experiment. First agree a dated M5 protocol for the recommended fixed-text stability mainline; historical return prediction and risk estimation are separately gated options.

**License:** no repository license is granted or implied. Permission to license or redistribute team code remains to be confirmed. Third-party model, data and dependency terms remain with their respective owners.
