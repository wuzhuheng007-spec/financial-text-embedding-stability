# Macro and systemic events → broad-market and technology transmission

Updated 2026-11-15, Asia/Shanghai. The user has clarified the research focus; implementation parameters below are proposals, not executed results.

## 1. Question and scope

Study how macroeconomic, policy, liquidity, geopolitical and pivotal technology-company events change expectations and may affect the US market, technology industries and individual firms. The event is the primary unit; firms are affected entities. A single-ticker CSV prefix is no longer the core corpus.

Retain representation stability: holding events and evidence fixed, how do models, prompts and chunking change classifications, transmission paths, firm exposures and downstream risk estimates? Establish relevant evidence before comparing models.

The user's executive, policy, rate-probability and borrowing-cost examples are hypothetical requirements, not verified news. Unverified numerical claims, personal allegations and rumors must not enter the fact table as confirmed events.

## 2. Event taxonomy and technology exposure

| Category | Priority events | Channels to investigate |
|---|---|---|
| Macro releases | CPI, core inflation, employment, wages, revisions | Policy expectations, real rates, demand |
| Monetary policy | Decisions, guidance, speeches, repricing | Discount rates, liquidity, USD, financing |
| Technology and industrial policy | Export restrictions, tariffs, data-center permits and power policy, concrete post-election measures | Market access, compute supply/demand, costs, capex |
| Geopolitics and energy | Shipping disruption, sanctions, escalation, supply changes | Energy, transport, inflation, risk premia |
| Financial stability and leverage | Credit spreads, margin pressure, deleveraging, banking/funding stress | Risk appetite, flows, contagion |
| Pivotal technology-chain events | Financing terms, capex changes, major customer/supplier shocks, governance events | Funding constraints, orders, delivery, confidence |

Initially cover US broad-market and technology exposures; rates, USD, energy, volatility and credit are channel variables. Candidate firms include the user's NVDA/CRWV interests and evidence-linked semiconductor, cloud, data-center and power/cooling firms. Candidate tickers are not verified historical security mappings. A company shock is not automatically systemic: document its concentration, funding or supply/demand channel.

Do not infer policy effects from party labels, adverse effects from competition alone, or US selling directly from Korean retail leverage. Every connection needs evidence or an explicit hypothesis label.

## 3. Evidence-backed event cards, not a single sentiment score

Each card separates four layers:
1. Facts: what was announced, first availability, source/version, evidence locations and fact/rumor/forecast status.
2. Expectations: actual, pre-release expectation, prior value and revision; meeting, action and timestamp for probabilities. Missing consensus remains unknown.
3. Transmission: signed hypotheses about rates, growth, liquidity, energy, credit and supply chains, each with support, horizon, conditions, counterevidence and a confidence basis.
4. Targets: broad market, technology industries and firms separately, with positive/negative/mixed/unknown implications and observable validation signals.

Text tone, expressed investor fear/optimism, inferred economic impact and realized market response are distinct variables. Separate quoted claims from model inferences. Uncalibrated model confidence is not a probability of a stock rising.

Candidate horizons are 1, 5 and 20 trading days, subject to a separately frozen financial protocol. Construction/capex channels may operate over months. Intraday claims require timestamped intraday inputs; daily prices cannot establish minute-level transmission.

## 4. Conditional transmission graphs

Hypothetically, inflation above a reliable pre-release consensus may reprice policy expectations and affect rates and technology valuations. Pricing already in place, growth information and conflicting components can change the response. Co-occurring returns do not establish causation.

Tighter financing could affect expansion and equipment orders. Existing contracts, cash, customer prepayments and alternative funding may weaken that path. Label every edge as source-stated, exposure-based inference or unverified. The first product version limits each scenario to two hops and three candidate paths; these are presentation limits, not causal guarantees. Show a supporting scenario, counter-scenario and next validation signal together.

## 5. Sources and availability

Use BLS releases/calendars, Fed decisions/speeches, company IR and SEC filings to verify events. Aggregators aid discovery and narrative coverage. Jin10/Hugging Face are candidate delivery channels, subject to product-specific coverage, timezone, first-publication, revision and retention checks. Account access does not establish historical coverage or text redistribution rights.

CME FedWatch probabilities are futures-implied, not Fed commitments. Store the meeting, action definition and snapshot time; today's page cannot substitute for a missing historical snapshot. BLS actual values do not supply survey consensus. Consensus needs a separate pre-release source. ALFRED vintages help with revisions, while series-specific and intraday availability still require checks.

Version text, values, expectations and timestamps. Cluster multiple reports of one event; article volume is not an independent-event count. No background monitoring is activated by this plan.

## 6. Selection and evaluation

The first development deliverable targets 12 relevance-review cards, aiming for two in each category from public material between 2026-05-15 and execution time. This is a development target, not an inferential sample size. Report shortages rather than inventing cases. Select using information available at the event, not subsequent large returns; retain quiet, low-surprise and opposite-response events.

The user's relevance selections are preference screening, not gold labels. Human adjudication must separately assess facts, expectation comparisons, firms, directions and edge support. Separate retrospective cases from forward collection. A pinned checkpoint does not establish its training cutoff; uncertain or overlapping training history permits retrospective interpretation only.

Later financial evaluation requires a continuous, rule-based event panel: scheduled releases pre-registered, unscheduled events admitted by fixed sources and ex-ante rules. Split by event cluster and time. Baselines include price/volatility history, numerical surprises and simple lexical/text methods; then add direct news and cross-firm channels. Prioritize incremental volatility/risk information before directional, covariance or trading studies. No labels or backtests run without admitted data.

## 7. Sequence and status

Stage 1: sources, selection and 12 candidate cards. Stage 2: small, separate fact-extraction steps and human verification. Stage 3: technology exposures and conditional graphs. Stage 4: frozen independent evaluation, stability and financial studies.

Keep the previous ten as LEGACY_ENGINEERING_DEV; the user need not complete their screening. Keep the failed wire-v2/LMFE callback path as PAUSED_LEGACY_BACKEND, with its failure preserved. It no longer blocks event discovery. The new event workflow does not inherit the old company-event schema as its admission gate.

Current status: scope clarified and roadmap written. New event cards, human gold, macro inference and financial experiments are not run. This is not a model-effectiveness report.
