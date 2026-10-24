# M6 engineering acceptance - BM25 and length controls

**Engineering implemented and self-checked; real-data M6 analysis is BLOCKED on omitted local inputs. No formal financial experiment ran.**

> Publication update (2026-10-25): The P6 upload blocker and four-blocker count below describe the original ZIP preparation on 2026-10-24. The user subsequently authorized upload of checked M6 code and documents to the existing public repository. See [publication review](2026-10-25_M6_publication_review.md) for the later Windows check, source-archive identity and CRLF/LF hash distinction. The original M6 research-data blockers remain.

Base repository: `wuzhuheng007-spec/financial-text-embedding-stability`, commit `6a1e3877045c486ea9467e42d3a36274a401300b`. Its accessible repository metadata reports public visibility; this round does not change visibility. The original Windows project and main branch are not overwritten.

## Plan versus actual

| Plan | Actual evidence | Status and permitted conclusion |
|---|---|---|
| P1: inspect existing source/config | Read current repository, pinned MiniLM config, original pooling and M5 drafts; downloaded curated source | VERIFIED source baseline; no new verification of omitted original raw files |
| P2: BM25 controls | `lexical.py`, formula/length/saturation/direction/geometry tests | VERIFIED software behavior on stated synthetic test fixtures only |
| P3: dense aggregation | Keyword-only positive chunk weights; default behavior unchanged; optional M6 dense path | VERIFIED numerical and synthetic wiring checks; real dense comparison NOT_RUN |
| P4: 10-K/10-Q input adapter | Explicit original form parsing, section labels, hashes, exact substring checks, fail-closed behavior | VERIFIED synthetic adapter tests; no live 10-Q acquisition or automatically validated semantic boundaries |
| P5: research route | M6 method, protocol addendum, README and roadmap | Prepared engineering route; M5 formal decisions remain TBD |
| P6: acceptance/publication | Check receipt, source checksums and review branch | Local acceptance completed. Remote tree upload BLOCKED by automatic approval review: current repository is public but prior sharing authorization specified private. A base-only branch exists; no M6 commit or PR was created. Checked ZIP delivered for review. |

## Checks actually executed

The four recorded acceptance commands and their complete normalized outputs are in [M6_checks.json](2026-10-24_M6_checks.json). Earlier development checks are not counted in this receipt.

- `python -m pytest -q -r s`: **40 passed, 2 skipped**. The skipped existing integration tests require the exact pinned model files, which are not downloaded here. Synthetic test text and a synthetic encoder only validate software behavior and are not company findings.
- `python -m compileall -q src scripts tests`: exit 0.
- `python -m finembed.m6 --help`: exit 0.
- `python -m finembed.m6 --section-index local_data/10q_mda_sections.json --output artifacts/m6/acceptance-missing-corpus-001`: expected exit 2, BLOCKED receipt and no metric files. This tests the missing-real-input gate; it is not a completed M6 corpus run.

`results/`, `figures/` and `configs/` retain their original bytes. The old root `MANIFEST_SHA256.json` is retained as the historical engineering-share snapshot and should be checked against the base commit, not blindly against modified M6 source files. `M6_CHECKSUMS.json` identifies the updated review snapshot. No raw filings, model weights, credentials or market data are added.

## Real-data blockers and next actions

1. Supply the original local M1 `corpus_index.json` and its saved raw/extracted files. The repository deliberately excludes those inputs. Their hashes are recorded in `results/PROVENANCE.json`; do not replace them with invented or silently re-downloaded text.
2. For dense runs, use locally verified pinned model files/dependencies from `configs/smoke.yaml`. Do not silently swap the encoder or tokenizer.
3. For 10-Q, acquire genuine original submissions, prepare reviewed MD&A/risk sections with exact source offsets, then use the section-index schema. Automatic original M1 discovery still selects 10-K only.
4. Agree on M5 formal sample/model/metric/time policies before treating any pilot as the formal study. b=0/0.75/1 and token weighting are engineering comparison settings, not selected winners.

There is no new measured improvement in representation quality, retrieval relevance, stability or financial returns in this round. BM25 directed retrieval and BM25-term-weight cosine remain distinct. Token weighting may reduce the relative contribution of a short tail chunk but is not assumed to improve semantics. Dividing a final vector by length cannot change its direction after L2 normalization.

## Claim and citation ledger

| Claim | Evidence | Status |
|---|---|---|
| Model configuration is pinned MiniLM and existing pooling is equal-chunk | `configs/smoke.yaml`, `src/finembed/model.py`, `docs/M2_M3_METHOD.md` | VERIFIED configuration/source; old raw inference not rerun |
| BM25 has separate saturation and document-length controls | Elastic official Similarity settings, directly opened 2026-10-24; exact custom formula in M6_METHOD.md | VERIFIED method definition, not SEC effectiveness |
| 10-Q Item 1 is not annual Business | SEC official Form 10-Q, Part I Items 1/2 and Part II Item 1A inspected | VERIFIED section distinction |
| M6 engineering checks passed | Check receipt and named tests | VERIFIED same-agent self-check |
| User-reported M0-M4 18/18 and M5 6/8 | Latest user message; no complete original checklist in curated repo | USER_REPORTED / NOT_VERIFIED here |
| BM25 or token weighting improves real SEC representations | No real M6 corpus/model run | NOT_VERIFIED; claim not authorized |

Primary-source URLs and the supported scope are in [M6_METHOD.md](../docs/M6_METHOD.md). No unverified Lucene source or unavailable Stanford page is used as claim support.

```text
Final status: PARTIALLY_SUPPORTED
Plan-execution gate: PASS (engineering scope only)
Experimental sufficiency gate: LIMITED (software fixtures; real-data study NOT_RUN)
Result provenance gate: PASS (recorded engineering checks; original research files not revalidated)
Citation verification gate: PASS (three opened primary sources; scoped claims only)
Commands actually run: 4 recorded acceptance commands; development commands excluded
Authoritative artifacts inspected: repository source/configuration/results; check receipt; three primary method sources
Unresolved blockers: 4 (local original corpus; pinned model environment for dense run; M5 scientific decisions; explicit authorization for public repository upload)
Claims authorized: software implementation and specified self-check outcomes
Claims not authorized: real SEC improvement, financial incremental value, independent audit, frozen M5 protocol
Worktree/user data preserved: YES (isolated share-copy checkout; original results unchanged)
```
