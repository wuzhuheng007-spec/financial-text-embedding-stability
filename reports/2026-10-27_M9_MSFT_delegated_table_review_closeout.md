# M9 Microsoft Item 2 table review and period gate

This is a local, descriptive review of the fixed Microsoft 10-Q (`0000950170-25-010491`), Part I Item 2 MD&A. The filing identity is independently listed in the [SEC filing index](https://www.sec.gov/Archives/edgar/data/789019/000095017025010491/0000950170-25-010491-index.html). The conclusions here concern the saved snapshot, not a first-public or tradable time. No financial experiment or remote push was performed.

## Decision provenance

| Item | Current record | Scope and limit |
| --- | --- | --- |
| Beginning, ending, body, MDA-1 to MDA-5 | 8 separate explicit researcher `ACCEPT` replies | Each is tied to the saved raw submission, extracted section, v4 review page, and applicable source/flattened table spans. The researcher did not supply an overall signature or review time. |
| MDA-6 to MDA-10 | 5 `ACCEPT` records after source checks under the user's prospective delegation | These are agent checks authorized by the user, not five item-specific personal researcher reviews and not an independent audit. |
| `PERIODS` | `NEEDS_SEMANTIC_REVIEW`; no ACCEPT | The saved ledger gives local phrase evidence but explicitly does not claim a complete semantic inventory of narrative, tables, and other periods. |
| Microsoft company review | `PENDING_HUMAN_REVIEW`; acceptance gate `NOT_MET` | 13 of 14 content items have ACCEPT records. No researcher-entered overall decision or review time is recorded. |

The exact original Chinese researcher replies and prospective instruction are retained in private local sidecars. English event files label their derivatives and preserve the decision basis. The latest [state](../artifacts/m9/20261027T011648Z-english-review/researcher_decisions_v1/MSFT/review_state_v14_en.json), [manifest](../artifacts/m9/20261027T011648Z-english-review/researcher_decisions_v1/MSFT/decision_manifest_v28_en.json), and [same-agent readback](../artifacts/m9/20261027T011648Z-english-review/researcher_decisions_v1/MSFT/readback_self_check_v14_en.json) are append-only local evidence. The prior MDA-1 to MDA-4 report is a historical snapshot, not the current tally.

## Source and mechanism checks

The original submission SHA-256 is `e92280011b21c42f7db19ec041faa11bfa8b7dd8b3e1e2aaa74f13fc1fc4276a`; the extracted Item 2 SHA-256 is `60653e4f3d91a82ed212b72c19ff618d769f5c379ac677603c57cefeb5404bf4`. The v4 browser review page SHA-256 is `fba2d2cc92626389ee3113856046c9b46e1c65d6b59b047cbdd16692642494f0`. The MDA-6 to MDA-10 event files record table-specific source HTML, flattened text, and browser handoff receipts; the local page was opened with the relevant tables expanded.

MDA-9's flattened text separates closing parentheses from some negative numbers. Its ACCEPT is therefore bounded to source-to-extraction review with that explicit formatting limitation: the flat text is not a ready numeric CSV. MDA-10 is a schedule of **expected future recognition of unearned revenue as of December 31, 2024**. The four dated future windows plus `Thereafter` sum to the displayed 48,045 million, but they are not realized historical quarterly results.

For `PERIODS`, the M8 [program ledger](../artifacts/m8/20261025T230645Z-four-10q/extraction_v4/MSFT/period_ledger.json) remains `PROGRAM_PERIOD_HINTS_ONLY_HUMAN_PENDING` with chapter period and start proxy null. The M9 [phrase ledger](../artifacts/m9/20261027T011648Z-english-review/source_period_en_v4/period_evidence.csv) has 54 Microsoft records: 20 broad quarterly, 27 year-to-date, 6 point-in-time/other, and 1 annual reference. Every saved Unicode span, excerpt, phrase, and excerpt SHA-256 matched the fixed Item 2 text in this check. The [exception record](../artifacts/m9/20261027T011648Z-english-review/source_period_en_v4/period_exceptions.json) says these are local classifications rather than a complete chapter inventory. That evidence is insufficient for a chapter-wide PERIODS ACCEPT. In particular, MDA-10's `Three Months Ending` is a future schedule, despite its broad quarterly tag.

## Execution and checks

The period closeout was generated with:

```powershell
python -X utf8 artifacts/m9/20261027T011648Z-english-review/researcher_decisions_v1/MSFT/append_period_review.py
```

The script rehashed the saved raw submission, primary HTML, normalized text, Item 2, review page/form, period ledgers, prior state, and prior manifest; it checked all 54 local period excerpts and created new files exclusively. Its [readback](../artifacts/m9/20261027T011648Z-english-review/researcher_decisions_v1/MSFT/readback_self_check_v14_en.json) reports `PASS`. A separate [same-agent verification log](../artifacts/m9/20261027T011648Z-english-review/researcher_decisions_v1/MSFT/closeout_tests.log) reports 90 passed hash, manifest-chain, count, and status checks. Neither check is an independent audit. No source-code edit was made for this closeout, so no new pytest run was required. An earlier one-off MDA-8 recorder had failed after writing a passing readback; an append-only recovery manifest retained that record, and no old evidence was overwritten. The current v14 append passed.

## Next gate

Create a source-bound semantic period inventory for the full Microsoft Item 2 narrative and ten tables. Keep comparative three-month and six-month flows, as-of balances, annual cross-references, and the future recognition schedule distinct. Recheck `PERIODS` after that inventory. The four new company-level reviews remain 0/4 accepted. No five-company 10-Q batch representation or formal M5 financial evaluation has run.
