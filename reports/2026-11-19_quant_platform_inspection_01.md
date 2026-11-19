# Quant platform inspection — 2026-11-19

## Evidence boundary
- User objective: continuous market collection and analysis, extensible factors/strategies, research and training.
- Repository inspected: wuzhuheng007-spec/financial-text-embedding-stability.
- Code baseline: 89ce71ac0038e97bbf8c1f95138791501b33bca6 (PR #4 head observed during inspection).
- Deployment receipt supplied by user: web 09041aa; feed 2c7f118.
- Attempted browser access to https://localhost:8443/ in the available cloud browser returned ERR_CONNECTION_REFUSED. This browser does not share the Windows machine's SSH tunnel. No production outage is inferred and no visual/live acceptance is claimed.
- No production credentials, server shell, or production database was available. No production change or supplier request was made.
- This is an initial code/receipt inspection, not a complete security, financial or model validation.

## Findings and actions

| ID | Priority | Evidence | Impact | Action/status |
|---|---|---|---|---|
| UI-01 | P1 | terminal_v20.js loadChart returns while chart.loading; selectSymbol clears data while old response is discarded | Rapid symbol changes can leave chart empty until a later refresh | Patched: coalesce forced reload and fetch the current selection when in-flight request settles |
| UI-02 | P1 | Same loading guard applies to range changes | Selected range may remain empty after old-range response returns | Covered by same patch and separate regression scenario |
| UI-03 | P1 | loadEarliest applies range fallback without checking current symbol | Late previous-symbol metadata can reset active range | Patched: cache coverage but do not mutate active controls for obsolete symbol |
| UI-04 | P1 | connState checks elapsed page-poll time only after explicit feed error | Hanging polling can leave stale connection indicator green | Patched: elapsed freshness independent of explicit rejection; update stale card/detail state on transition |
| DATA-01 | P1 | reference_v20.py regular/legacy branch selects latest session row, then associates prev_close_price by price equality only | Equal prices across messages/dates do not establish quote identity; percentage may use an unrelated baseline | Open: require raw identity or validated symbol/session/trading-date/time linkage; equal-price collision regression required |
| DATA-02 | P1 | personal_feed_cache.py applies 20-second heartbeat threshold to every provider except moomoo_snapshot; supplied Jin10 config receipt describes 120-second polling | Potential false stale state when Jin10 is re-enabled, depending on actual heartbeat update cadence | Open: verify worker heartbeat cadence and derive per-provider threshold; not a confirmed live failure |
| FUNC-01 | P1 | UI v20 receipt explicitly says old related-news timeline was not carried over | Product regression in price/event investigation workflow | Open: restore chart-linked event list and selection, with correct source times |
| NET-01 | P1 | Supplied diagnostics tested five Jin10 IPs and several mainland controls | Multiple timeouts support broader network-path investigation, not proof that all mainland addresses are unreachable or exact loss at last responding hop | Open: narrow conclusion, verify cloud egress/security group and provider ticket evidence |
| OPS-01 | P1 | Earlier localhost tunnel refusal in user screenshot; current browser isolation | Access depends on client tunnel; latest persistent recovery status unverified here | Open: Claude to verify Windows tunnel lifecycle and end-to-end browser access |
| PLATFORM-01 | P2 | Supplied UI acceptance establishes quote/news/research views, not end-to-end factor/training workflow | Attractive dashboard alone does not demonstrate research platform readiness | Not accepted: inventory existing dataset snapshots, factor versions, backtesting, experiment jobs and training artifacts before adding systems |
| UI-05 | P2 | Receipt lists no English translations; only after-hours live testing | Other sessions and locale behavior not production-verified | Open: bounded acceptance on relevant real sessions; no synthetic pass labelled live |

## Executed verification
Runtime: Node.js v24.22.3 in isolated scratch environment; deterministic network/clock doubles.
Production closure is evaluated before bootstrap; rendering is stubbed. These tests validate state/async behavior, not layout, browser performance, provider data or server deployment.

Command:
`node tests/terminal_v20_regression.cjs [path/to/terminal_v20.js]`

Five scenarios:
1. Symbol switch during pending fetch.
2. Range switch during pending fetch.
3. Late old-symbol coverage response.
4. Hanging feed poll after 15-second freshness deadline.
5. Initial explicit poll failure remains disconnected.

Unmodified baseline: 1 passed, 4 failed.
Patched source: 5 passed, 0 failed.
These are results for this one regression suite, not evidence of general system reliability.
No Django suite, real browser product session, throughput benchmark, model training or trading test ran.

## Implementation boundaries
- Only terminal UI JS changes and regression tests; no quote calculation or backend schema modifications.
- Existing 15-second UI freshness threshold retained; this checks browser data-poll freshness, not exchange quote latency.
- Forced reloads coalesce; an indefinitely hanging chart request still requires a separate bounded timeout/cancellation improvement.
- No dependencies added.
- No main-branch merge or deployment.

## Next execution order for Claude
1. Inspect/cherry-pick this audit branch on the latest working branch; preserve concurrent research commits.
2. Run regression suite and relevant existing checks. Deploy web only using the existing rollback procedure.
3. Validate fast symbol/range switching and simulated stalled UI fetch in an isolated browser session; confirm live quotes and SEC remain available.
4. Fix DATA-01 with provenance-linked quote/base selection and date-collision tests; do not fill missing bases with guesses.
5. Verify Jin10 heartbeat semantics and restore event timeline when a valid news source is available.
6. Audit existing research components and build one vertical workflow: versioned dataset -> one versioned factor -> chronological backtest with costs -> recorded experiment -> result page.
7. Add training only after dataset availability times, chronological splits, labels and artifact lineage are established. Preserve current research scope and do not infer general performance from one factor/dataset/seed.
8. Each completed step records plan, actual action, input/config/code versions, outputs and limitations in the repository.

## Requirements for the research vertical workflow
- Separate immutable research snapshots from mutable display caches; ingestion must not be blocked by training jobs.
- Record event/source time, received time and earliest known availability independently.
- Reconcile daily bars by instrument, exchange calendar, session, adjustment mode and currency.
- Factors have definition, parameters, code version, input version, output schema and missing-value policy.
- Backtests use chronological splits, appropriate separation for overlapping future labels, explicit next executable time and costs; out-of-sample results remain separate from development results.
- Jobs expose state, logs, cancellation, resource limits and output artifact IDs; start with a simple queue and avoid premature distributed infrastructure.
- Failure/empty states remain concise in UI; detailed diagnostics stay in Settings/logs.
