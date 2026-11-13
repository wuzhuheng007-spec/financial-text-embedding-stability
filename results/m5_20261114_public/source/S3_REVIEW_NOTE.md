# S3: existing originals and three event identity chains

Status: `SOURCE_FACTS_REVIEWED_IDENTITY_CHAIN_INCOMPLETE`. All checks used frozen local inputs. No SEC original was downloaded again, no prior Item 1 file or discovery ledger was changed, and no event was formally admitted. The deterministic review script is `scripts/review_m5_source_closure_s3.py`; its row-level output, excerpts and proposed text are in ignored `local_only/` files. The safe results are `s3_ambiguity_cases_safe.json` and `s3_identity_chain_safe.json`.

## Four previously ambiguous original filings

| Anonymous case | Source inspection | Decision |
| --- | --- | --- |
| A1 | One filer. The substantive body is headed **Items 1 and 2. Business and Properties**, distinct from the table of contents. The candidate span ends at the actual Item 1A heading. Opening, middle and end contain business/property discussion. | A source-exact combined-section proposal is saved privately. Whether this combined section meets the project's Item 1 text rule is pending. |
| A2 | Two distinct SEC `FILER` blocks identify a property trust and its operating partnership. The one primary 10-K has a substantive Item 1 discussing their joint business. | Keep entity attribution and the joint-filing rule pending. No automatic first-filer choice. |
| A3 | One filer. The body uses a two-line `Item 1 – Business` heading and a two-line `Item 1A – Risk Factors` heading; the earlier one-line occurrence is an index entry. Opening, middle and end are substantive business discussion. | A source-exact body-span proposal is saved privately, separate from the existing extractor output. The original remains ambiguous until a text-rule decision. |
| A4 | Two distinct SEC `FILER` blocks identify a property trust and its operating partnership. The one primary 10-K's Item 1 explicitly discusses the trust's business through the partnership. | Keep joint-filing attribution pending. No automatic first-filer choice. |

The four original bodies were rehashed against their saved SEC body hashes. Four provisional text spans were saved byte-exact from rendered source with offsets and SHA-256; these are **review proposals**, not accepted texts. The two single-filer alternate headings explain extractor misses. The two dual-filer cases are genuine legal-entity branches.

## Three frozen Item 1 + finite-end examples

The S2 selection took the first three, sorted by filing date, CIK and accession, from five available `Item1-supported ∩ finite-bracket ∩ no unresolved-end candidate-ID hit` events. Source rows and identifiers remain private. Each entry below is a review of one candidate event, not a sample-performance result.

| Hop | E1 | E2 | E3 |
| --- | --- | --- | --- |
| Original SEC filer → cover security | One filer; NYSE common **partnership units** and an exact candidate Item 1. | One filer; NYSE common stock and an exact candidate Item 1. | One filer; NYSE Class A common stock and an exact candidate Item 1. |
| CIK → CUSIP | Saved exact CIK/CUSIP observation with a date span containing the filing. That span is observation time, **not historical link validity**. | Same observation-only status. | Same observation-only status. |
| Dated issuer/security source → candidate security ID | Issuer and security source rows have literal start/end dates strictly around the filing; security name, ticker and CUSIP match source observations. Source endpoint convention and dated CIK-to-issue applicability remain unaccepted. | Same structural evidence, with a common-stock class. | Same structural evidence, with a Class A common-stock class. |
| Saved CCM finite row → candidate ID | One finite row literally brackets the filing and its PERMNO/CUSIP/ticker align. The query's Header CIK join time and release are unknown; its `LINKPRIM` code does not select the project's primary security. | Same source limitation. | Same source limitation. |
| Formal result | Historical issuer-to-security `UNSUPPORTED`; primary security `UNSUPPORTED`; ordinary-share eligibility unresolved, especially for partnership units. | Historical issuer-to-security `UNSUPPORTED`; primary security `UNSUPPORTED`; ordinary-share eligibility pending an accepted source rule. | Same formal status as E2. |

**Smallest next identity evidence:** establish a dated issuer CIK/CUSIP-to-security/issue relationship with source/version and interval semantics, bind the saved CCM query's Header join timing, then freeze the project's security class and primary-selection rule. A name or ticker match alone cannot close any missing hop. The three original filings support their own filer/class/ticker facts; they do not establish a CRSP historical identity chain.

## Verification and boundaries

Run in the original repository with private sources available:

```powershell
$env:PYTHONPATH='src'
python scripts/review_m5_source_closure_s3.py --out artifacts/m5_source_closure_20261114_0400
```

The command checks the prior candidate ledger, S2 selection, R3 intersection, SEC body hashes and the registered CIK/CUSIP, security, issuer and CCM CSV hashes. It verifies exact source row ordinals, Item 1 byte equality and literal date brackets. The two safe JSON files contain only anonymous case/event labels and source-hash bindings. `security_id` stays null in the discovery ledger; all formal labels, prediction scores and returns remain `NOT_RUN`.
