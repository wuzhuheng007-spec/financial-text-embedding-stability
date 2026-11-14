# N1 manual company–event annotation template

Status: **FIELD-DESIGN PROTOTYPE** (2026-11-14, Asia/Shanghai). This public file is a codebook, not an annotation result. Keep article text, row-level annotations, and source identifiers in the approved local workspace. Manual inspection is not model inference; news-model inference and financial evaluation remain **NOT_RUN**.

## Unit and review order

Create one record per **article version × event × potentially affected company**. An article may describe several events, and an event may affect several companies differently. Review at most ten usable-body records in their fixed saved-source order for the first field check. Read only the sampled version's title/body and available source metadata; do not consult later revisions, prices, or outcomes. Record a separate `NO_IDENTIFIABLE_EVENT` or `NO_SUPPORTED_COMPANY` disposition if no company–event record can be made.

## Local record fields

| Field | Allowed form / decision rule |
| --- | --- |
| `article_version_ref` | Local reference to the exact saved article version and its file hash; never publish the row-level reference. If the source supplies no revision ID, mark it `NOT_PROVIDED` rather than inventing one. |
| `text_kind` | `ARTICLE_FIELD_PROSE_COMPLETENESS_UNKNOWN`, `VERIFIED_FULL_ARTICLE`, `SUMMARY`, `TITLE_ONLY`, or `UNKNOWN`, based on observed content and provenance. A nonempty `Article` field alone does not prove publisher-complete original text. An existing sentiment label is a separate source field, not article prose or a manual impact label. |
| `time_value`, `time_precision`, `time_zone_basis` | Transcribe the source value; use `DATE_ONLY` when applicable. Record a documented offset/zone or `UNKNOWN`. Never add a market-open or intraday time to a date-only value. |
| `event_description`, `event_type` | Short, literal description of the reported occurrence and a provisional type; use `UNKNOWN` when the text does not support one. Do not include an unsupported causal market claim. |
| `target_company_text`, `ticker_candidate`, `identity_status` | Company name as written and any source ticker candidate. `identity_status` is `TEXT_ONLY`, `CANDIDATE_TICKER`, or `HISTORICAL_LINK_VERIFIED` with a separate dated identity source. A ticker alone does not verify historical security identity. |
| `company_role` | `SUBJECT`, `TARGET`, `COUNTERPARTY`, `OTHER`, or `UNKNOWN`; describe the company's role in this event. |
| `evidence_location` | Private article-version-relative span offsets or paragraph/sentence locator for the event and company link. Prefer an exact locator over an uncited paraphrase; keep quoted text local. |
| `impact_direction` | `POSITIVE`, `NEGATIVE`, `MIXED`, `NO_CLEAR_IMPACT`, or `UNKNOWN` for the stated or hypothesized company effect, **not** a predicted stock return. Do not assign a competitor a negative effect by rule. |
| `impact_path` | `DIRECT`, `INDIRECT`, or `UNKNOWN`. For `INDIRECT`, record the dated relationship or exposure evidence in a private companion field. |
| `statement_status` | `EXPLICIT_IN_ARTICLE`, `ANNOTATOR_INFERENCE`, or `UNKNOWN`. An inference is not an article assertion; record its premise and uncertainty. |
| `uncertainty_note` | Concise source ambiguity, competing interpretations, or missing time/identity evidence. |
| `unable_to_judge_reason` | `NONE`, `NO_USABLE_TEXT`, `NO_EVENT`, `NO_COMPANY_LINK`, `AMBIGUOUS_TARGET`, `AMBIGUOUS_DIRECTION`, `INSUFFICIENT_RELATION_EVIDENCE`, `TIME_AMBIGUITY`, or `OTHER` with an explanation. Preserve `NO_CLEAR_IMPACT` separately from extraction failure or absent news. |
| `annotator_provenance` | Reviewer kind, review date, guide version and independent human-adjudication status. Codex review drafts are `AI_ASSISTANT_MANUAL_REVIEW_DRAFT`, never human gold labels or extractor-model output. |

## Field-check acceptance note

For the bounded first pass, record which fields were observable, which required judgment, and each unresolved ambiguity. The examples test whether the fields can represent the sampled text; ten reviewed articles, one source, or one annotator cannot establish general extraction accuracy. Keep financial admission separate: historical company–security identity, point-in-time availability, prices, and a frozen protocol are still required before any return evaluation.

中文字段速览：`event_description` 事件，`target_company_text` 受影响公司，`evidence_location` 证据位置，`impact_direction` 影响方向，`impact_path` 直接/间接，`statement_status` 明确陈述/标注者推断，`unable_to_judge_reason` 无法判断原因。原文、逐行标注和私人身份映射只留本机。
