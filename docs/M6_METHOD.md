# M6: BM25 lexical controls and long-filing length sensitivity

Status: engineering implementation; **M5 scientific protocol remains DRAFT**. No new financial experiment or demonstrated improvement. See [execution contract](M6_PLAN_EVIDENCE.md) and [acceptance report](../reports/2026-10-24_M6_engineering.md).

## Revised route

M0-M4 remain the existing engineering demo. M5 retains the researcher/professor decisions. M6 can proceed in parallel as reversible software preparation: audited sections -> lexical fit on exactly those rows -> BM25 length-control arms -> optional dense aggregation arms -> descriptive comparison -> review. Financial prediction and risk estimation remain behind M5 timing/model/universe gates.

The three meanings of normalization must not be conflated:

1. BM25 document-length correction: `b` changes term weights according to the document's lexical-token length relative to corpus average.
2. Dense vector L2 normalization: divides a vector by its Euclidean norm. It does not make the model read omitted report text. Dividing a final vector by document length before L2 normalization cancels algebraically and cannot change cosine neighbors.
3. Chunk aggregation weighting: equal weighting gives a short final chunk the same vote as a full chunk. Body-token weighting changes that vote, using the pinned tokenizer's body tokens only. It may help or hurt, so compare rather than claim an improvement.

## BM25 variant, scope, and outputs

`lexical.py` implements an explicitly specified BM25 variant:

`idf(t) = log(1 + (N - df(t) + 0.5)/(df(t) + 0.5))`

`w(t,d) = idf(t) * f(t,d)*(k1+1) / (f(t,d) + k1*(1-b+b*L(d)/avgL))`

`score(q,d) = sum(w(t,d) for t in unique(q))`

Engineering defaults: k1=1.2 and b in {0,0.75,1}; these are not tuned on study outcomes or scientifically frozen. b=0 is the required no-length-correction control. Tokenizer `ascii_alphanumeric_lower_v1` uses `[a-z0-9]+`, lowercases, retains numbers, splits punctuation/hyphens, and has no stemming or stopword list. It is deliberately distinct from the dense WordPiece tokenizer; both lengths must be labeled. Empty texts and duplicate/invalid records fail, with no silent exclusions.

Fit df/IDF and average length only on the exact included rows. For temporal applications, fit on the permitted historical corpus only; never use future filings for those statistics. Do not mix 10-K Business with 10-Q MD&A in the same length pool. Small-corpus IDF remains a limitation. Whole documents used as queries contain all their unique terms: this is a descriptive lexical comparator, not a validated search relevance benchmark.

Two distinct outputs:

- `bm25_directed_scores_b*.csv`: query-document rankings; not symmetric, not bounded cosine, not a covariance/kernel. Self scores remain in the export but are excluded from peer selection.
- `bm25_termweight_cosine_b*.csv`: cosine between sparse document vectors with coordinates `w(t,d)`. This is a separately named BM25-term-weight representation, **not standard BM25 retrieval similarity**. A scalar document correction would cancel under cosine; this termwise saturation is nonlinear, so it need not cancel.

This custom formula is not asserted to reproduce Lucene scores bit-for-bit; Lucene analyzer, norm encoding, query handling and score conventions can differ. Standard BM25 has a query/document role and does not turn documents into neural dense embeddings.

## Dense arms

Keep the existing pinned MiniLM model/tokenizer and 128/192 nonoverlapping body chunks. Existing `aggregate(v, normalize_chunks, normalize_documents)` behavior is unchanged. Its new keyword-only `weights` accepts finite positive weights matching chunk rows.

`--dense` compares `equal_chunks` and `body_token_weighted`, with the existing chunk-normalization setting and final document L2 normalization. Each chunk is encoded once per length and reused across pooling arms. Exclude CLS/SEP/padding from aggregation weights, but retain the original model's attention-mask pooling convention. Save vectors, cosine tables, token/chunk/tail counts and pinned model identity. This weights contextual chunk representations; it is not mathematically identical to encoding the full document as one sequence. No cross-model seed claim or Procrustes experiment is added.

No weights are downloaded automatically. Missing corpus, files or model dependencies are explicit BLOCKED outcomes. Synthetic test encoders verify software wiring only.

## 10-K and 10-Q boundaries

`primary_text(raw, expected_form)` and `raw_header(raw, accession, expected_form)` now accept original 10-Q as well as 10-K. Defaults remain 10-K. Existing automatic discovery and Item 1 extraction remain **10-K-only**. This change does not claim an automatic 10-Q section extractor or live 10-Q ingestion.

Supported reviewed section labels:

| Form | Sections |
|---|---|
| 10-K | ITEM_1_BUSINESS, ITEM_1A_RISK_FACTORS, ITEM_7_MDA |
| 10-Q | PART_I_ITEM_2_MDA, PART_II_ITEM_1A_RISK_FACTORS |

10-Q Part I Item 1 contains financial statements, not the annual Business section. Comparisons of related narrative sections, or new versus repeated quarterly content, require their own design. Quarterly risk factors may only update annual disclosures; do not assume they have identical coverage.

For externally reviewed sections, use a JSON `entries` array with one record per company:

```json
{
  "entries": [{
    "ticker": "YOUR_TICKER", "cik": "TEN_DIGIT_CIK", "accession": "REAL_ACCESSION",
    "form": "10-Q", "section": "PART_I_ITEM_2_MDA",
    "raw_path": "original_submission.txt", "raw_sha256": "ACTUAL_SHA256",
    "text_path": "reviewed_section.txt", "text_sha256": "ACTUAL_SHA256",
    "start_char": 0, "end_char_exclusive": 1, "boundary_reviewed": true
  }]
}
```

This is a schema illustration, not runnable data. Replace every placeholder and the illustrative offsets with genuine values. Relative paths resolve from the index location. The adapter verifies original-form/accession header, raw/text hashes, and the exact substring of the rendered primary document. The human semantic-boundary attestation is recorded as USER_ATTESTED, not independently validated. Historical public availability is NOT_VERIFIED. Use one company/file/section per run; repeated filings and different form/section strata must be split explicitly.

## Commands

From the repository root, install `python -m pip install -e '.[embed,test]'`. For a relocated M1 corpus, fix its path references in a new index and retain the old index/hash; never overwrite original evidence.

```powershell
# Existing audited five-company original M1 files, still on your computer:
python -m finembed.m6 --config configs/smoke.yaml --corpus artifacts/runs/20261019T003627Z-d3043c3e/corpus_index.json --output artifacts/m6/lexical-001

# Same frozen corpus; additionally compare dense pooling, using verified local model files:
python -m finembed.m6 --config configs/smoke.yaml --corpus artifacts/runs/20261019T003627Z-d3043c3e/corpus_index.json --dense --output artifacts/m6/dense-001

# Separately reviewed original 10-Q MD&A corpus:
python -m finembed.m6 --section-index local_data/10q_mda_sections.json --output artifacts/m6/10q-mda-001

python -m pytest -q
```

Each output directory must be new. Receipts contain input/config/source hashes, parameters, status, model identity if used, and output hashes. A failed dense stage may leave completed lexical outputs; the receipt remains BLOCKED/FAILED and must not be interpreted as a completed dense study. No original text is copied into outputs. Do not publish unreviewed raw input or artifacts.

## Proposed formal outcomes - not executed

Use upper-triangle Spearman with exact-value average ties, per-company top-k overlap, and documented invalid outcomes. Self is excluded; exact similarity ties break by document ID. Require k < N-1 so neighbors do not trivially contain every other company. No p-values, confidence intervals or quality thresholds are produced by this small engineering comparison. Length-related changes are descriptive; better relevance requires frozen queries and independent relevance judgments. Stability alone does not establish useful semantics or financial improvement. Do not tune b or aggregation using held-out financial outcomes.

Only later consider hybrid BM25+dense retrieval, rank fusion or query-guided section selection. BM25 needs an explicit query for selection; selecting only chunks favored by a query changes coverage and must be reported. Rank fusion combines rankings, not uncalibrated raw BM25 and cosine scores. These hybrid methods are not implemented or executed in M6.

## Verified primary references

- Elastic, **Similarity settings**, official documentation (accessed 2026-10-24): https://www.elastic.co/docs/reference/elasticsearch/index-settings/similarity - BM25 k1 saturation, b document-length normalization and documented defaults. Not evidence of improvement on SEC reports.
- SEC, **Form 10-Q**, official form (accessed 2026-10-24): https://www.sec.gov/files/form10-q.pdf - Part I Item 1 financial statements; Item 2 MD&A; Part II Item 1A risk factors.
- Sentence Transformers, **all-MiniLM-L6-v2 model card**, pinned revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md - encoder identity and model-specific handling. Not historical contamination clearance.

All three were opened directly during preparation. No claim of novelty, superiority, independent audit or formal scientific approval is made.
