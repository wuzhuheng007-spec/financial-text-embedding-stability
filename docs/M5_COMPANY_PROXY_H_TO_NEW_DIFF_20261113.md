# Checked H-to-new-draft changes

**Status:** `DESIGN_PREPARATION_ONLY / FULL_PROTOCOL_PROPOSED`. This table enumerates all 62 operations in the [checked patch](../configs/m5_protocol_h_to_company_proxy_patch_20261113.json) from the exact [original H candidate](../configs/m5_protocol_design_h_20261113.json) to the [new company-proxy candidate](../configs/m5_protocol_company_proxy_conditional_20261113.json). Each replace/remove operation checks the original leaf value; an added leaf must not exist. The patch does not alter the five development CIKs, the inherited numeric/model contracts, or the original round-8 and H/R bytes. `selected_design` describes preparation direction only; the new exact full protocol remains unaccepted and financial execution remains unauthorized.

| JSON pointer | Operation |
| --- | --- |
| `/chronology/clarification_status` | add |
| `/chronology/purge` | replace |
| `/chronology/refit` | replace |
| `/current_engineering_evidence/selected_design_preparation_only` | add |
| `/current_missing_evidence/approved_prediction_observation_count_reason` | replace |
| `/current_missing_evidence/complete_feature_and_label_price_reason` | replace |
| `/current_missing_evidence/decision_time_item1_vintage_reason` | replace |
| `/current_missing_evidence/delisting_outcome_reason` | replace |
| `/current_missing_evidence/first_public_dissemination_reason` | replace |
| `/date_coverage_contract/per_horizon_mask` | add |
| `/date_coverage_contract/position_reference` | replace |
| `/date_coverage_contract/required_total_window` | add |
| `/decision_and_label/close_availability_rule` | replace |
| `/decision_and_label/label_available_after` | replace |
| `/decision_and_label/missing_or_delisted_label_policy` | replace |
| `/decision_and_label/primary_label` | replace |
| `/decision_and_label/recommended_decision` | replace |
| `/decision_and_label/security_join/delisting_rule` | replace |
| `/derived_from` | add |
| `/design_id` | replace |
| `/design_review/acceptance_source` | replace |
| `/design_review/clock/exact_historical_text_availability_proven` | add |
| `/design_review/clock/exact_item1_public_or_no_later_than_by_18_et` | remove |
| `/design_review/clock/first_public_time_proven` | add |
| `/design_review/clock/fixed_text_bytes_required` | add |
| `/design_review/clock/historical_finalization_time_is_required_for_proxy` | add |
| `/design_review/clock/intraday_anchor_time` | add |
| `/design_review/clock/intraday_anchor_time_reason` | add |
| `/design_review/clock/proxy_researcher_accepted` | add |
| `/design_review/clock/rule` | replace |
| `/design_review/clock/status` | replace |
| `/design_review/clock/timezone` | add |
| `/design_review/conclusion_limit` | replace |
| `/design_review/design_choices_accepted` | add |
| `/design_review/horizon_mask_choice/selected` | replace |
| `/design_review/horizon_mask_choice/selection_scope` | add |
| `/design_review/identity_gate/why_blocked` | replace |
| `/design_review/minimum_missing_roles` | replace |
| `/design_review/missing_exit_interpretation` | replace |
| `/design_review/required_price_window` | replace |
| `/design_review/schema` | replace |
| `/design_review/scope` | replace |
| `/design_review/selected` | replace |
| `/design_review/selection_scope` | add |
| `/design_review/trade_close_field_preference` | replace |
| `/design_review/unit_and_claim` | replace |
| `/execution_gates` | replace |
| `/prediction_arms/common_sample_scope` | add |
| `/preparation_authorization` | add |
| `/prior_accepted_scope/decision_source_reused_or_created_this_round` | replace |
| `/prior_accepted_scope/three_new_design_choices_for_preparation` | add |
| `/purpose` | replace |
| `/sample/admission` | replace |
| `/sample/event_unit` | replace |
| `/sample/starting_and_event_denominators` | add |
| `/sample/starting_cohort_price_coverage_reselection` | add |
| `/schema_version` | replace |
| `/selected_design` | replace |
| `/selected_design_scope` | add |
| `/supersession_status` | replace |
| `/time_interpretation/recommended` | replace |
| `/time_interpretation/strict_historical_as_of_status` | replace |

Full old/new values and preconditions are in `checked_h_to_new_protocol_patch.json`. The exact original H source must match before the local patch is applied.
