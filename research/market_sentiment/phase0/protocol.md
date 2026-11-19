# Research protocol — 2026-11-19 (Asia/Shanghai)

Question: Which observable combinations distinguish medium-term market sentiment reversals from transient rebounds in US and mainland Chinese equities over approximately 1996–2026?

Scope: Historical literature/event review and candidate signal specification. A quantitative efficacy claim requires inspected machine-readable point-in-time data and an executed walk-forward evaluation; it is not authorized by a historical narrative.

Unit: market-week; individual news-event/security reactions form explanatory features. US and China must be evaluated separately.

Proposed horizons (not fitted): 4, 8, 12 weeks. Parameters are illustrative until frozen before a backtest.

Plan-to-evidence matrix:
| ID | Planned operation | Expected evidence | Validation | Allowed claim | Blocker |
|---|---|---|---|---|---|
| P1 | Verify current Nvidia/Anthropic/Fed claims | Open official releases/filings and direct reporting | Inspect dates, wording, attribution | Facts vs causal hypotheses distinguished | Missing/ambiguous source |
| P2 | Review representative US/China turning episodes | Open central bank, regulator, IMF/BIS and primary research | Match source to mechanism, distinguish hindsight | Source-backed case interpretation | No basis for universal timing |
| P3 | Review established factor families and access limits | Open original papers and official indicator methodology | Check predictive target and population | Literature support with limits | Vendor performance is not reproduction |
| P4 | Specify candidate state features and evaluation | Explicit definitions, lag and control requirements | Check feasibility and leakage | Proposed research design only | No point-in-time raw panel |
| P5 | Probe public historical-data access | Actual fetch status and inspected data if available | Record failures, never substitute silently | Data feasibility only unless a run executes | Network/data entitlement/history |
| P6 | Save report, source ledger and audit | Versioned local files and persisted deliverable | Inspect produced text and save return | Deliverables exist | Save failure |

Acceptance: factual claims supported by opened sources; no reported strategy performance without raw result provenance; event selection labeled illustrative, not exhaustive; inferred causes labeled interpretation; current IPO liquidity claim not asserted as causal fact.

Current event statements from user are USER_REPORTED until separately checked.

## Frozen exploratory pilot (before outcome calculation)

Plan update: public OECD/FRED China series begins January 1999, not 1996. A thirty-year A-share quantitative test is therefore not possible using this input. OECD country share-price indexes are proxies, not a verified all-A / S&P 500 total-return investment universe. They are current-vintage monthly observations with potentially revised history. Monthly averaging and release delays preclude treating changes as executable portfolio returns.

P5 pilot: US 1996–2025; China 1999–2025. Exclude 2026 outcomes. This is descriptive screening, not a point-in-time backtest. Use end-of-month VIX; OECD monthly country price index. Outcome y(t)=P(t+4)/P(t+1)-1, a three-month index change after a one-month gap. Endpoint must be within December 2025. No dividends, costs, or investability claim.

Rules fixed before calculation:
- all months baseline;
- VIX month-end >=30 (panic_level);
- last-three-month maximum month-end VIX >=30 and VIX(t)<VIX(t-1) (panic_easing);
- panic_easing AND P(t)/P(t-3)>1 (easing_plus_momentum);
- P(t)/P(t-3)>1 (momentum_only control).

Report each rule, including negative results, for full sample and 2016–2025 calendar segment. This later segment is a temporal stability check, not an independently unseen holdout. Report signal-month counts and contiguous episode counts; do not conflate months with independent crises.

Uncertainty: circular moving-block bootstrap of entire paired outcome/mask monthly rows, block length 12 months, 2000 replications, numpy default_rng seed 20261119. Report 95% percentile interval for conditional mean minus unconditional mean. Do not assert significance or optimized factor efficacy; no multiple-testing-adjusted discovery claim. Overlapping outcomes and few stress clusters imply limited independent evidence.

No credit-spread pilot: downloaded BAMLH0A0HYM2 only starts November 2023, consistent with current FRED history limits. Do not silently replace it or claim historical credit confirmation tested.

Execution outcome: pilot.py ran successfully once; 20 rule/market/segment summary rows were generated. A separate self-check implementation re-read raw files and reconciled all signal dates, eligible counts, means and input hashes. This is self-checking, not independent validation. Sparse-mask bootstrap resamples with zero selected observations are excluded; valid replication counts are reported. Do not rely on the narrow intervals from rare groups as robust statistical evidence.
