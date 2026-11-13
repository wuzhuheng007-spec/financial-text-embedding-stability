# M5 simple-split price policy proposal

**Status:** `PROPOSAL_NOT_ACCEPTED / SYNTHETIC_ONLY`. This note is a candidate rule for the retrospective company-event design. It does not amend the byte-bound [full M5 candidate](../configs/m5_protocol_company_proxy_conditional_20261113.json), whose adjustment and nonordinary-action fields remain `null` and whose research status remains `PROPOSED / researcher_accepted=false / NOT_RUN`. It creates no event mask, adjusted real price, return label, or admitted observation.

## Verified public definitions and their limit

The July 2026 [CRSP US Stock Databases Calculations & Index Methodologies Guide](https://indexes.morningstar.com/api/docs/6a454f3183e511651648cf28) was opened as a 21-page PDF, 405,499 bytes, SHA-256 `694c5978a75d034e74aecb46d08795a24729c7e083754cdc7a3dd0ca2dafa163`. Its printed page 5 defines adjusted price as raw price divided by a cumulative factor, says adjustments apply on the ex-distribution date, and warns that unknown action gaps make adjusted values missing. Printed page 7 says a simple split's period price factor is its distribution price factor plus one. Printed page 11 gives a two-for-one example with distribution factor 1 and period factor 2. The official [distribution-type dictionary](https://www.crsp.org/wp-content/uploads/appendix/FlagType_AT.html) calls `FRS` a forward or reverse split. The guide also says price factors can cover other distributions, including spin-offs and rights. Its page-5 forward-recursion layout is ambiguous, so this proposal does not transcribe that recursion.

These public definitions do not establish the release, field equivalence, completeness, or action history of the saved WRDS exports. In particular, a saved `DlyFacPrc`, `DlyRetx`, `DisFacPr`, or cumulative factor is not certified here as a split-only series. The type and factor of each real event require source-bound confirmation before any use.

## Proposed narrow algebra, derived by this project

For a separately confirmed simple split at ex-date `e`, let `f_e` be its source-confirmed distribution price factor and `q_e = 1 + f_e`. Require finite `q_e > 0`. For raw traded close `P(t)` and an arbitrary common-basis session `B`, propose:

```text
if t <= B: A_B(t) = P(t) / product(q_e for t < e <= B)
if t >  B: A_B(t) = P(t) * product(q_e for B < e <= t)
```

An empty product is one. This is a unit-conversion derivation for **independently confirmed simple splits**, not a general CRSP adjustment formula or a computed M5 label. The interval is open on the earlier session and closed on the later one. A split with ex-date `d` changes earlier lookback units, but is absent from the `(d,d+h]` factor interval. Distinct-date simple conversions multiply. A price ratio on a common basis is invariant to the choice of `B` when the same complete action context is used.

## Candidate exclusion and exit handling

- Require one source-confirmed simple split per ex-date and a complete relevant action history. Reject an invalid factor, an unknown event or gap, multiple actions on one ex-date, mixed action types, or a nonordinary distribution pending a separate sourced rule. Do not infer `FRS` from a factor value alone. Confirm an ordinary-cash classification from the saved release before treating it as outside this price-only adjustment; no cash amount is added to this candidate price target.
- Require a finite positive, source-valid traded close at every fixed applicable session from `d-20` through each horizon's `d+h`; keep each endpoint fixed. Missing middle prices, quote-only substitutes, unresolved repeated Daily keys, unsupported market sessions, and unknown action coverage remain unscored with recorded reasons. Do not fill, shift, or silently remove them.
- Under the existing **conditional complete-window** preparation direction, do not substitute a terminal value for a missing close. A known but unresolved exit that affects a required price observation and an unknown outcome remain distinct attrition reasons. The exact known-terminal price-only formula is needed only if a different terminal-substitution or full-cohort estimand is later proposed; exit coverage and required-window validity are still open gates now.
- Retain the starting-company and potential-event denominators and year-by-reason attrition. The five- and 21-session masks remain separate; F0/F1/F2 share the same rows within a horizon. A complete-window comparison would describe that conditional subset, not the original full cohort.

The handoff's `simple_split_reference.py` and `test_simple_split_reference.py` were replayed locally with Python 3.11: eight artificial tests passed. The helper has no real-file interface. Its `synthetic_fixture` and `action_context_complete` arguments are caller declarations; they do not prove source completeness or event identity. The tests validate algebra only. The exact saved-release mappings, complex actions, same-day composition, issue calendar, and full protocol still need separate evidence and a version-bound research decision before real labels.
