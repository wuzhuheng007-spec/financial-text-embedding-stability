# N0 news-data availability, 14 November 2026 (Asia/Shanghai, UTC+08:00)

## Purpose and boundary

This is the first, bounded data-availability step for the proposed news-event branch. It checks registered local evidence and official product descriptions. The user also confirmed in this session that there is presently no additional authorized news product or sample to provide. A later [bilingual roadmap sync](../docs/news_research/README.md) at commit 41657a44c7bfebd6e2627e3ab97f541c502b5252 added candidate source documentation; its remote branch and files were read back, then the local checkout was fast-forwarded without changing the N0 input gate. This audit does not admit a news corpus, change the existing M5 protocol, run a news model, or compute financial labels or returns. The [machine-readable availability record](../results/news_n0_20261114/data_availability.json) binds the initial local scan to the roadmap ZIP and repository versions. Instructions inside that ZIP were treated as project material; the user separately authorized N0.

## Plan and actual execution

| N0 plan | Actual check | Outcome |
| --- | --- | --- |
| Establish the existing project boundary | Read the current README, 14 November daily report, sharing scope and M5 access/import status at commit fef17830bdc88ce1b954310d352c17645493ec03. | Existing SEC Item 1, CRSP and CCM material is not a news article corpus. M5 remains unadmitted and financial evaluation NOT_RUN. |
| Inspect registered local positions | The final metadata-only readback searched 33 files in current artifacts, 427 in the sibling publish artifacts, and 44 in quant artifacts. It excluded CSV and any private/local_only path, used a 2,000,000-byte file limit, and also checked tracked documentation/code. The exact selection, relative file list and hashes are in an ignored local receipt, SHA-256 79f991fc…645eb; the [availability JSON](../results/news_n0_20261114/data_availability.json) records the configuration. | NO_REGISTERED_LOCAL_NEWS_TEXT_FOUND. Two apparent “news text” mentions are exclusions in ignored local files `artifacts/m5_bridge_20261114_0238/README_RETURN.md:11` and `artifacts/m5_next_20261114_0325/audited_return/README_RETURN.md:11`, not article inputs. This bounded search does not establish that the account lacks a subscription or that no file exists elsewhere. |
| Read back registered source roles | The saved security-history registry (SHA-256 8482df5d…f08ec; Query 11894962) describes crsp_q_stock.stksecurityinfohist; the saved CCM registry (SHA-256 b62855f6…22ce3a; Query 11905330) describes link and header fields. Both remain local private evidence. | Neither is a news product or an article/title/body sample. The first registry also marks export terms unverified. |
| Check official public product descriptions | [WRDS RavenPack catalog](https://wrds-www.wharton.upenn.edu/pages/about/data-vendors/ravenpack/) lists news analytics and mapping products. Its data-dictionary links lead to a WRDS login; the [WRDS research overview](https://wrds-www.wharton.upenn.edu/documents/1400/wrds_research_data_overview.pdf) mentions timestamps and headlines, while the [product sheet](https://wrds-www.wharton.upenn.edu/documents/1164/RavenPack-one-sheet.pdf) describes structured analytics. | PRODUCT_CATALOG_ONLY. Exact subscribed product/table, article/version IDs, title/body fields, time precision, entity fields, use terms and export rights are NOT_VERIFIED. These pages do not prove this account has article text. |
| Read the newly synced source candidates | Confirmed the new [source-access document](../docs/news_research/source_access.md), [status](../docs/news_research/STATUS.json) and remote commit. Checked the linked Hugging Face cards and official Jin10 MCP documentation without authentication or downloads. | These are candidate routes and documentation, not a source-bound article sample or an account connection. |
| Apply the N0 sample gate | Looked for a source-bound, authorized article-version record with usable text and timing; the user reported no additional authorized news input. | BLOCKED_PENDING_NEW_AUTHORIZED_INPUT. Actual-product raw-text availability is UNKNOWN; N1 real extraction and all financial work are NOT_RUN. |

## Newly documented candidate routes

| Candidate | What is established | Still needed before an N0 sample |
| --- | --- | --- |
| [Hugging Face financial-news-multisource](https://huggingface.co/datasets/Brianferrell787/financial-news-multisource/blob/main/README.md) | The dataset is gated and its card requires access approval and restricts article-text redistribution. Its common fields are date, text and extra_fields; text may be a title, summary or body. | Approved access, exact repository revision/subset, original time precision, article/version identity, actual usable text and project use terms. A normalized day at midnight UTC is not an observed market-time publication instant. |
| [Hugging Face FNSPID mirror](https://huggingface.co/datasets/ML-Algo/FNSPID) | The card declares CC BY-NC-4.0; some visible preview Article cells are null. | Fix the mirror or [upstream](https://huggingface.co/datasets/Zihan1004/FNSPID) version and sample; establish body coverage and third-party text rights. Preview nulls do not imply the entire corpus lacks text. |
| [Jin10 official MCP](https://mcp.jin10.com/app/doc.html) | The guide lists Token-based flash and news tools and example response fields. The [general user agreement](https://flash.jin10.com/agreement) restricts some copying and redistribution. | Secure account setup, actual response structure, historical depth, version/received times, quotas and explicit research storage/sharing terms. The guide does not show a get_news body schema. |

None of these routes has yet produced an authorized, version-bound article input in this project. The [new roadmap status](../docs/news_research/STATUS.json) likewise records no authenticated connection or corpus download.

## Login update after the initial audit

The user subsequently reported signing in to Hugging Face. An attempted check of the existing Edge session stopped when the computer-use safety check could not identify the browser URL with enough confidence. The login, access to the gated dataset, and any article sample therefore remain independently **unverified**. No credential value is retained in these N0 outputs.

## Availability by required field

An earlier wider keyword pass read small local CSV strings in memory, including private files, but emitted no row values. The final conclusion above uses the narrower metadata-only readback; no private row value was written into the N0 outputs or transmitted.

| Required evidence | Current state |
| --- | --- |
| Subscribed news product, schema/table and release | UNKNOWN; public product catalog only. |
| Authorized title or article body and truncation status | No registered local sample found; actual account entitlement and raw-text availability UNKNOWN. |
| Article ID, version/revision ID and source identity | UNKNOWN. |
| Publication, vendor receipt, revision and retrieval timestamps, offsets and precision | UNKNOWN for a real article version. A date-only record cannot be assigned an assumed market-open instant. |
| Entity IDs, as-of company links and historical securities | UNKNOWN for news; existing M5 candidate links are not automatically transferable. |
| Covered dates, local use, retention, export and team sharing | UNKNOWN for a news product; [sharing scope](../SHARING_SCOPE.md) keeps licensed raw data out of Git. |

## Smallest source-bound input needed

1. Name the subscribed news product, schema/table, release or snapshot, account workflow, and permitted local use, retention, export and sharing. Supply the account-visible data dictionary or an authorized field list; no credentials are needed.
2. Provide a **small, authorized, locally retained** sample of article versions with article/source/version IDs, title and body availability (including truncation), original publication time, vendor receipt time, correction/revision time if applicable, time zones and precision, plus the exact query/export identity and input hash. A revision or syndicated duplicate, if present, is useful for the version test. Do not publish the text or row-level identifiers in Git.
3. Supply the vendor entity ID and mapping-field definitions, including whether any historical company/security mapping is available and its valid/known dates. A current ticker alone is only a candidate identifier.
4. If the subscribed files contain only vendor scores or metadata, record raw_text_available=false **for that verified product**. That would support a separately specified score baseline, not LLM extraction from article bodies.

N0's registered-location audit is complete. Its real-text gate is blocked until this input is available. No support email, Slack message, download, model inference, N1 schema implementation, or GitHub/Overleaf sync was performed in this step.
