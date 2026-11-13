# M5 H/R protocol design comparison

**Status:** `ENGINEERING_ONLY / PROPOSED_DESIGNS`. Neither design is selected or researcher accepted. Both machine candidates retain `PROPOSED / researcher_accepted=false / NOT_RUN`; their formal cohort and admitted observation counts are `null`. The unchanged [round-8 candidate](../configs/m5_financial_protocol_round8.json), SHA-256 `f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38`, remains the baseline. The separately accepted retrospective direction A and three preparation rules do not accept a complete M5 protocol.

The complete review candidates are [H](../configs/m5_protocol_design_h_20261113.json), which preserves the original company-level question, and [R](../configs/m5_protocol_design_r_20261113.json), which proposes a narrower fixed-snapshot observation association. H is the recommendation **for preserving that research question**, conditional on obtaining its missing evidence. R must be selected separately; easier source availability does not turn its association into historical company identity or an automatic replacement for H.

## Invariants mechanically checked against round 8

| JSON path | Retained rule |
| --- | --- |
| `development_evidence_only` | S2/S3 cannot select a favorable dense setting; use the pre-existing 128-body-token, zero-overlap, equal-chunk MiniLM/ONNX reference. |
| `sample.planning_cohort_hash_rule` and `sample.development_cik_exclusions` | The proposed deterministic at-most-200-CIK ordering and five excluded development CIKs are byte-for-byte equal as JSON values. No actual cohort is selected. |
| `sample.filing_decision_years`, `chronology` | Proposed 2020–2023 training, 2024 validation and held-out 2025 with chronological purge and train-only refits. |
| `features`, `prediction_arms` | F0/F1/F2, 32 lexical SVD and dense PCA coordinates, numerical-rank rules, float64 Ridge, alpha grid and common-row requirement are unchanged. |
| `metrics` | The same held-out squared-error reduction, secondary metrics, denominator/failure definitions and no economic-value claim. H and R would attach different *population interpretations* to those unchanged formulas. |
| `decision_and_label.primary_horizon_subsequent_sessions`, `alternative_horizon_subsequent_sessions`, `primary_label`, `label_start`, `label_end` | The proposed 5-/21-session fixed endpoints and split-adjusted traded-close **price** return formula remain. Neither price adjustment nor a label was computed here. |

## Baseline-to-design changes shared by H and R

Each entry below is a changed or added JSON path relative to round 8; both designs carry the same review-only change unless noted in the H/R table. No source-evidence update rewrites the round-8 original.

| JSON path | Change and reason |
| --- | --- |
| `schema_version`, `source_round8_candidate_sha256`, `supersedes_proposed_candidate_sha256`, `supersession_status` | Version 3 review candidates bind the exact round-8 bytes; no previous candidate is superseded merely by drafting. |
| `prior_accepted_scope.*`, `selected_design`, `design_id` | Preserve the earlier narrow acceptances while keeping the new design decision `null`. |
| `current_engineering_evidence.*` | Record source-bound security/issuer/CIK/Daily/distribution/delist/calendar roles, the attribute-v3 summary hash, and `null` formal event/cohort fields; these are evidence layers, not formal admission. |
| `current_missing_evidence.historical_security_master_reason` | A 2020 acquisition extract and histories exist, while eligible operating-common membership, dated CIK relation, primary issue and market applicability remain unproven. |
| `current_missing_evidence.complete_feature_and_label_price_reason` | Eight Daily segments and a saved derivation exist; canonical close, corporate actions, exits and common mask remain unaccepted. |
| `current_missing_evidence.independent_session_calendar_reason` | A retrospective XNYS baseline exists; event-market/MIC applicability remains unknown. |
| `current_missing_evidence.delisting_outcome_reason` | Delist candidates exist without an accepted complete exit outcome rule. |
| `current_missing_evidence.dividend_manifest_reason` | Distribution candidates exist; the primary target still excludes ordinary dividends and other-distribution treatment is unresolved. |
| `current_missing_evidence.decision_time_item1_vintage_reason` | An original-accession, fiscal-period and exact Item 1 byte-version event manifest is still missing. |
| `current_missing_evidence.first_public_dissemination_reason` | Filing/acceptance dates do not prove the first-public instant; a no-later-than source or accepted proxy has not been chosen. |
| `current_missing_evidence.approved_prediction_observation_count_reason` | The full design, event mask and financial protocol remain unaccepted; admitted count stays `null`. |
| `date_coverage_contract.without_independent_calendar` | Having an XNYS retrospective baseline does not confirm the exchange calendar for a security with unknown event-time market. |
| `design_review.reference_date`, `source_interval_endpoint_inclusivity`, `endpoint_sensitivity_diagnostics` | `2020-01-01` stays a proposed diagnostic reference; closed and left-closed/right-open counts are explicit sensitivity conventions, not supplier validity proof. |
| `design_review.preparation_code_pattern.*` | `EQTY`/`COM`/`NS` and `ACOR`/`CORP` are an unaccepted joint *literal-code diagnostic*; `USIncFlg` and `PrimaryExch` exclusions remain `null`. |
| `design_review.horizon_mask_choice.*` | Recommend separate horizon-specific masks common to F0/F1/F2; a shared 21-session mask is a named alternative. `selected=null` because the choice changes the five-session population. |
| `design_review.required_price_window`, `trade_close_field_preference`, `split_adjustment_formula`, `nonordinary_distribution_rule`, `exit_terminal_value_rule` | Require each fixed session from `d-20` through `d+h` to have finite positive close; prefer traded `DlyClose`, no automatic `DlyPrc` quote fallback. The action/terminal formulas remain `null`. |
| `design_review.formal_original_filing_item1_event_manifest_sha256`, `formal_cohort_count`, `admitted_observation_count`, `financial_labels_computed`, `financial_training_or_scoring_run` | Missing event manifest and formal sample remain `null`; no label or financial computation was performed. |

## H versus R: every differing JSON leaf path

The table lists all 36 scalar/list leaf differences between the two full candidates, including the distinction between a missing key and an explicit `null`. A path present in only one `design_review` object is shown as *not applicable* on the other side. Exact strings and array contents remain in the two JSON files.

| JSON path | H: dated company-primary target | R: fixed-snapshot association | Why it matters |
| --- | --- | --- | --- |
| `design_id` | `H_DATED_COMPANY_PRIMARY_SECURITY` | `R_FIXED_SNAPSHOT_UNIQUE_ISSUE_OBSERVATION_ASSOCIATION` | Makes the estimate and evidence gate explicit. |
| `purpose` | Retrospective company filing-event comparison. | Conditional issue-observation association. | R no longer estimates a company-primary relation. |
| `time_interpretation.recommended` | Retains `RETROSPECTIVE_FIXED_BYTES`. | Fixed-source-snapshot retrospective association. | Neither supports historical tradability. |
| `sample.planning_cohort` | At most 200 CIKs from verified proposed 2020 eligible company master. | At most 200 CIKs from uniquely associated source-snapshot observation candidates. | Population and representativeness change. |
| `sample.event_unit` | Original CIK filing/fiscal period paired with date-valid primary issue. | Original filing observation paired with unique fixed-snapshot issue. | Date-valid identity is required only for H's claim. |
| `sample.admission` | Requires dated identity, market and 18:00 text/close evidence. | Proposes a conditional observed-window event with declared proxy and ambiguity ledger. | Different admissions and denominator meaning. |
| `decision_and_label.recommended_decision` | Original 18:00 ET gate requires exact text and finalized `d` close available by then. | 18:00 ET date-plus-lag analytic anchor, not demonstrated public/close availability. | R cannot be described as an as-of prediction. |
| `decision_and_label.close_availability_rule` | Source time or no-later-than evidence required by 18:00 ET. | No historic 18:00 claim; accept a separate retrospective price rule first. | R relaxes an evidence gate only with an explicit narrower claim. |
| `decision_and_label.missing_or_delisted_label_policy` | Keep broad cohort blocked until a source-backed exit rule or separately accepted narrower target. | Proposes complete-observed-window conditional association with full attrition accounting. | R does not measure full-cohort performance. |
| `decision_and_label.security_join.unit` | One date-valid listed issue per CIK event. | One unique source-snapshot CIK/PERMNO observation association. | Unit changes. |
| `decision_and_label.security_join.recommended_rule` | Dated CIK bridge plus documented primary and event-time market. | Exact-CUSIP code-2/3 candidates and strict structural one-to-one in declared reference set. | Candidate uniqueness cannot certify historical ownership. |
| `decision_and_label.security_join.multiple_candidate_rule` | Preserve classes; lacking documented primary is `AMBIGUOUS_SECURITY`. | Retain all parallel/repeated edges; no arbitrary tie-break. | Both keep ambiguity, under different identity claims. |
| `decision_and_label.security_join.delisting_rule` | Retain all company/issue exits without zero fill. | Retain full starting denominator and reasons for conditional exclusions. | Avoids survivor substitution in either design. |
| `decision_and_label.security_join.alternative_rule` | Unaccepted predecision 60-session dollar-volume option; early-2020 buffer may be insufficient. | `null`; volume cannot make an observation link date-valid or primary. | Changes security-selection rule and data needs. |
| `execution_gates` | Adds dated CIK/primary/market and strict 18:00 text/close gates to existing gates. | Requires separately selected proxy, unique snapshot relation, full attrition ledger and common F0/F1/F2 mask. | Neither is currently runnable. |
| `design_review.unit_and_claim` | Company filing with dated primary issue. | Fixed-snapshot filing-observation relation. | R changes the scientific target. |
| `design_review.identity_gate.event_time_cik_security_evidence` | `null`: dated bridge evidence is still missing. | *Not applicable*. | A CIK observation edge cannot fill H's valid-time gate. |
| `design_review.identity_gate.dated_company_primary_issue_evidence` | `null`: company-level primary basis is missing. | *Not applicable*. | Listing exchange code alone cannot choose the issue. |
| `design_review.identity_gate.event_market_mic_evidence` | `null`: event-market evidence is missing. | *Not applicable*. | The XNYS baseline does not certify all issues. |
| `design_review.identity_gate.why_blocked` | Observation dates do not prove validity/primary. | *Not applicable*. | H has no certified admitted count. |
| `design_review.identity_gate.quality_2_or_3_exact_cusip_candidate` | *Not applicable*. | Existing candidate screen, not accepted identity. | R can use it only as a structural candidate. |
| `design_review.identity_gate.strict_one_to_one_relation` | *Not applicable*. | Diagnostic pending accepted rule. | Same-CIK duplicate edges and different-CIK ambiguity must be distinguished. |
| `design_review.identity_gate.historical_validity_claim` | *Not applicable*; H requires actual evidence. | `false`. | R expressly forgoes this claim. |
| `design_review.identity_gate.company_primary_issue_claim` | *Not applicable*; H requires actual evidence. | `false`. | R expressly forgoes this claim. |
| `design_review.clock.rule` | Strict availability gate. | Analytic proxy. | Source time versus declared convention. |
| `design_review.clock.status` | Availability evidence unmet. | Proxy not accepted. | Neither clock is an execution authorization. |
| `design_review.clock.exact_item1_public_or_no_later_than_by_18_et` | `null`: exact-text availability remains unproven. | *Not applicable*. | H's 18:00 text gate is retained. |
| `design_review.clock.finalized_close_available_by_18_et` | `null`: final close availability remains unproven. | *Not applicable*. | H's 18:00 market-data gate is retained. |
| `design_review.clock.exact_2020_vintage_proven` | *Not applicable*. | `false`. | Later fixed extraction is not a historical vintage. |
| `design_review.clock.first_public_time_proven` | *Not applicable*. | `false`. | SEC Filing Date is not a public timestamp. |
| `design_review.clock.proxy_researcher_accepted` | *Not applicable*. | `false`. | No proxy decision was supplied. |
| `design_review.clock.strict_historical_model_availability_claim` | `false`. | *Not applicable*. | The fixed later model has no as-of claim. |
| `design_review.missing_exit_interpretation` | Broad company cohort and source-backed exits remain required. | Complete observed window changes population and requires year/reason attrition. | R's narrower estimand must be named. |
| `design_review.sixty_session_primary_alternative` | Unaccepted, early-2020 prehistory may be insufficient. | *Not applicable*. | No volume-based primary inference for R. |
| `design_review.minimum_missing_roles` | Dated bridge, primary/MIC, strict event text/close and price/exit rule. | Accepted structural association, proxy/vintage, applicable sessions and price/exit rule. | Different minimal acquisitions/decisions. |
| `design_review.conclusion_limit` | No company-level admitted count until all gates pass. | Conditional fixed-snapshot association only. | Neither permits general performance claims. |

## Evidence roles and interpretation boundaries

| Role | Held engineering evidence | H still needs | R still needs |
| --- | --- | --- | --- |
| Security/issuer attributes | Starting security and later histories; source-bound `SecurityType` etc. Prior nulls were unassessed placeholders, not missing columns. | Release-specific code meanings and an accepted company-level eligibility rule. | Same code meanings for any claimed literal subset; source-snapshot code-pattern rule must be accepted. |
| CIK/issue | Historical CUSIP candidates and first/last filing observations; provider code-2/3 screening is an accepted **preparation** rule only. | Independent date-supported CIK-to-security relation and documented company-primary issue, with event-time market/MIC. | Accepted fixed-snapshot strict-unique observation relation, declared endpoint convention, ambiguity ledger and full denominator. No ownership/primary claim. |
| Event text/time | No formal original-accession/fiscal-period/Item1 version event manifest. | Exact text vintage and finalized close evidence by proposed 18:00 ET; no-later-than evidence is not first-public time. | Byte-fixed later extraction and researcher-accepted filing-date-plus-lag proxy; do not call it contemporaneously available text. |
| Market/price/actions/exits | Retrospective XNYS calendar baseline, eight Daily segments and Distribution/Delist candidate material exist with saved engineering receipts. | Applicable event-time market, canonical traded close, split/nonordinary action and broad exit treatment. | Applicable analytic session axis, same price/action meanings and predeclared complete-window attrition. |

The [current public CRSP CIZ guide](https://indexes.morningstar.com/api/docs/6a71c517e35d02a71403136d) defines `DlyClose` as a regular-session traded close and `DlyPrc` as a price that can use a bid/ask average. It does not authenticate this saved WRDS export's exact version, supply a project adjustment formula, or choose an exit outcome. `DlyFacPrc` is not certified here as a split-only cumulative factor; `DlyRetx` is not substituted for the proposed target. The five development firms remain excluded. The raw 2020 acquisition set is not a qualified market/cohort; later-only issues stay outside that set unless a separate prospective design is approved.

## Three decisions for one researcher review

| Priority | Recommended choice | Alternative and change | Evidence and execution gate |
| --- | --- | --- | --- |
| 1. Target and identity | Select **H** if the original company-level question is retained; require dated CIK/primary/market evidence before a formal cohort. | Select R only as a separately accepted fixed-snapshot conditional association, with structural uniqueness and a narrower conclusion. | H bridge/primary roles are missing; R has candidate edges but no accepted relation. Both admitted counts remain `null`. |
| 2. Event clock and text vintage | For H, retain proposed exact-text and finalized-close availability by 18:00 ET; document no-later-than evidence where the first-public instant is unknowable. | For R, explicitly accept a filing-date-plus-lag analytic proxy and later fixed-byte text, without an as-of claim. | Neither has a formal original Item 1 event/vintage manifest; the proxy must not be inferred from SEC acceptance time. |
| 3. Price, exits and common mask | Prefer traded `DlyClose`; require each fixed session from `d-20` through `d+h`; recommend horizon-specific masks common to F0/F1/F2 after an accepted split/action/exit rule. | A single 21-session mask for both horizons changes the five-session population. A complete-window R association changes the target population; H requires broad-cohort exit treatment. | No current adjustment/terminal formula or final event mask. No results, labels or test-set selection were used to choose these options. |

The unaccepted predecision 60-session dollar-volume alternative for H would require a full earlier window; the held November–December 2019 buffer cannot be presumed sufficient for early-2020 decisions. Selecting H or R, accepting one exact clock/text rule, and accepting one complete price/exit/mask rule must precede any financial execution. The joint feasibility diagnostic uses the proposed 2020 reference date only to size *candidate* sets; it is neither a formal cohort count nor a substitute for these decisions.
