# Four-company Form 10-Q MD&A researcher review guide

## Scope and source versions

This guide accompanies the versioned English review packet under the M9 run directory. It is a handoff for reviewing four fixed filings. The saved M8 source and extraction files are the evidence; an English page is a derivative display, not a replacement for either file. The M8 run, its `review_v7` packet, and earlier researcher records remain unchanged.

| Company | Central Index Key (CIK) | Fixed accession | Report period end | Filing date |
| --- | --- | --- | --- | --- |
| Microsoft Corporation (MSFT) | 0000789019 | 0000950170-25-010491 | 2024-12-31 | 2025-01-29 |
| Amazon.com, Inc. (AMZN) | 0001018724 | 0001018724-25-000036 | 2025-03-31 | 2025-05-02 |
| Alphabet Inc. (GOOGL) | 0001652044 | 0001652044-25-000043 | 2025-03-31 | 2025-04-25 |
| Meta Platforms, Inc. (META) | 0001326801 | 0001326801-25-000054 | 2025-03-31 | 2025-05-01 |

The filing identity, raw submission hash, primary HTML hash, normalized primary text hash, extracted section hash, and displayed page hash must agree with the version summary and the saved local files. If any identity or hash differs, stop review of the affected item and preserve both versions for diagnosis. The saved raw SEC files stay local under the project's current sharing policy. The review pages depend on these local evidence files even when they display excerpts.

## Review sequence

Begin with **MSFT, Part I, Item 2, Management's Discussion and Analysis of Financial Condition and Results of Operations (MD&A)**. Review one item at a time. For each item, compare the original filing display with the normalized primary text and the extracted section. Give an explicit `ACCEPT`, `REVISE`, or `REJECT` for that item; describe an exact source location or content problem for `REVISE` or `REJECT`.

1. **Beginning:** Check that the displayed start is the real Part I, Item 2 body, that a table-of-contents occurrence is excluded, and that preceding Part I, Item 1 content did not enter the section.
2. **Ending:** Check that the final MD&A content is present and the following item did not enter the extraction.
3. **Substantive content:** Check paragraphs, headings, page breaks, cross-references, unusual characters, and omitted or repeated content.
4. **Tables and adjacent notes:** For every numbered body table, compare the original HTML rows and columns with the extracted text. Check units, dates, sign conventions, percentages, footnotes, and notes immediately outside the table element. AMZN also has two heading or layout tables; these are displayed separately from its six substantive body tables.
5. **Reporting periods and exceptions:** Check each identifiable narrative block or table on its own evidence. The page may show quarterly, year-to-date, trailing-twelve-month, annual-reference, or unresolved periods in the same MD&A section. An unresolved interpretation stays unresolved until supported by the source and review.

Continue with AMZN, GOOGL, and META only as individual items are presented. Opening a page, exporting a blank form, or saying “continue” is not a research decision. The form begins with `PENDING_HUMAN_REVIEW`; it must not contain a prefilled reviewer, review time, signature, or decision. An exported form is a local record that requires version validation and a separate project acceptance check.

## Interpreting the evidence

- `PROGRAM_BOUNDARY_PASS` describes a software boundary check against saved files. It is not a researcher acceptance.
- `AGENT_CHECKED` describes inspection by the same assistant that prepared the packet. It is a self-check, not an independent audit.
- `RESEARCHER_CONFIRMED` is reserved for an explicit, version-bound researcher decision. Unanswered items remain `PENDING_HUMAN_REVIEW`.
- The earlier AAPL review has nine explicit item-level `ACCEPT` decisions. A separate overall signature and researcher-entered time were not supplied. AAPL is outside the four-company M9 packet.
- A filing's acceptance time is not, by itself, proof of the first public or tradable instant. Neither the entire MD&A's coverage start nor a narrative period can be inferred from the previous filing's period end plus one day.
- The saved M8 comparison between a newly embedded primary document and an earlier standalone copy establishes a specific structural difference. It does not establish historical snapshot equivalence.

The review is for source integrity and usable section boundaries. It does not validate text embeddings, forecasting performance, market value, or freedom from look-ahead bias. No five-company batch representation calculation or financial experiment is part of this handoff.

## Saving a decision

Use the exact review page and form version displayed in the packet. Record each decision separately, keeping the original response and any later revisions. A real decision must be tied to the filing accession, raw submission hash, extracted section hash, and page hash. The researcher must supply their own identity and actual review time if a signed overall form is needed. The assistant must not invent or backfill either field.

If an item needs revision, preserve the original extraction and decision, make a new version, and ask for review again only for affected items. Do not silently move accepted decisions to a changed page or source. When all required items pass, a separate acceptance step may determine whether the filing becomes a reviewed corpus item.

Keep exported software-test records distinct from genuine researcher records. A blank export/import validates form behavior only. Importing a record with a different source, section, or page hash must fail.
