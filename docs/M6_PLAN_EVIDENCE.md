# M6 execution contract - 2026-10-24

M6 is an engineering extension, not a frozen scientific experiment. M5 choices remain open.

Research question proposed: how do lexical document-length correction and dense-vector pooling choices affect fixed-text company representations? Unit: document/section, with company dependence retained. Comparisons: BM25 b=0 versus b=0.75 versus b=1, plus equal-chunk versus body-token-weighted pooling. Metrics proposed: common-row similarity agreement and peer overlap; no financial target or significance claim is authorized.

| ID | Operation | Authoritative input | Artifact | Acceptance | Blocker |
|---|---|---|---|---|---|
| P1 | Inspect repository and existing model/aggregation | GitHub main 6a1e3877045c486ea9467e42d3a36274a401300b | Source snapshot and change list | Preserve original results and M5 gates | Missing repository access |
| P2 | Implement explicit BM25 variant and vector cosine | Tokenized documents in one frozen fit corpus | lexical.py; numerical tests | Hand-calculated score; b=0 length invariance; invalid input rejection | None for engineering tests |
| P3 | Extend dense aggregation with optional token weights | Existing aggregate API | model.py; regression tests | Existing default unchanged; short-tail weighting checked | Real chunk vectors unavailable for a real-data comparison |
| P4 | Accept verified 10-K/10-Q sections without false section mapping | Saved original SEC bytes, source offsets and hashes | section adapter and M6 CLI | Reject 10-Q Item 1 as Business; exact source substring; no silent exclusions | Original raw/text bytes absent from curated repository |
| P5 | Update roadmap, protocol addendum and commands | Existing M5 drafts plus user request | M6_METHOD.md and revised roadmap | Proposals distinguished from executed outcomes | Scientific parameters await researcher/professor |
| P6 | Run local checks and publish review branch | Source snapshot and test fixtures | Test log, receipt, commit and draft PR | Re-read remote files; no main overwrite | Publication access if unavailable |

Only engineering tests may use clearly labeled synthetic fixtures. They must never be saved as company findings. No model weights, real filings, market raw data or credentials are published. No formal financial experiment is planned in this round. BM25 retrieval scores are directed and are not covariance or cosine matrices. BM25 term-weight cosine is a separately named lexical representation.

Evidence classes: user-pasted M4/M5 completion counts are USER_REPORTED, not rerun here. Repository source/configuration and checked result tables are inspected evidence; underlying excluded original files remain NOT_VERIFIED. Proposed M6 settings are engineering defaults, not professor-approved parameters.
