"""Prepare or check a JSON protocol draft. No data, labels, model or network work."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

DESIGN = 'H_COMPANY_PROXY_CLOCK_COMPLETE_WINDOW_20261113'
ROUND8 = 'f1fa28949353adea6aa179854441b48658415ef577a006fe76bd01c779ddaa38'

class ContractError(ValueError):
    pass

def require(condition, code):
    if not condition:
        raise ContractError(code)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def pairs(items):
    value = {}
    for key, item in items:
        require(key not in value, 'DUPLICATE_JSON_KEY')
        value[key] = item
    return value

def decode(data):
    try:
        return json.loads(data, object_pairs_hook=pairs)
    except ContractError:
        raise
    except (ValueError, UnicodeError):
        raise ContractError('INVALID_JSON') from None

def validate(candidate):
    try:
        d = candidate
        require(d['status'] == 'PROPOSED', 'FULL_STATUS_CHANGED')
        require(d['researcher_accepted'] is False, 'FULL_PROTOCOL_ACCEPTED')
        require(d['formal_financial_experiment_status'] == 'NOT_RUN', 'FINANCE_STATUS_CHANGED')
        require(d['design_id'] == DESIGN and d['selected_design'] == DESIGN, 'WRONG_DESIGN')
        require(d['source_round8_candidate_sha256'] == ROUND8, 'WRONG_ROUND8_SOURCE')
        a = d['preparation_authorization']
        require(a['design_choices_accepted'] is True and a['full_protocol_accepted'] is False and a['financial_execution_authorized'] is False, 'AUTHORIZATION_SCOPE_ERROR')
        require(a['exact_local_new_protocol_sha256'] is None, 'SELF_HASH_PLACEHOLDER_CHANGED')
        r = d['design_review']
        require(r['design_choices_accepted'] is True and r['researcher_accepted'] is False, 'DESIGN_FULL_ACCEPTANCE_CONFLATED')
        require(r['financial_labels_computed'] is False and r['financial_training_or_scoring_run'] is False, 'FINANCIAL_WORK_CLAIMED')
        for k in ['formal_cohort_count', 'admitted_observation_count', 'formal_original_filing_item1_event_manifest_sha256', 'split_adjustment_formula', 'nonordinary_distribution_rule', 'exit_terminal_value_rule', 'source_interval_endpoint_inclusivity']:
            require(r[k] is None, 'UNRESOLVED_FIELD_FILLED')
        i = r['identity_gate']
        for k in ['admitted_count', 'dated_company_primary_issue_evidence', 'event_market_mic_evidence', 'event_time_cik_security_evidence']:
            require(i[k] is None, 'HISTORICAL_IDENTITY_INFERRED')
        require(d['decision_and_label']['security_join']['unit'] == 'one time-valid listed common-share security identifier per CIK filing event', 'H_IDENTITY_REPLACED')
        require('one sourced primary common-share issue' in d['decision_and_label']['security_join']['recommended_rule'], 'H_PRIMARY_REQUIREMENT_LOST')
        clock = r['clock']
        require(clock['proxy_researcher_accepted'] is True and clock['fixed_text_bytes_required'] is True, 'PROXY_OR_TEXT_RULE_LOST')
        require(clock['historical_finalization_time_is_required_for_proxy'] is False, 'STRICT_FINALIZATION_GATE_REINTRODUCED')
        require(clock['intraday_anchor_time'] is None, 'INTRADAY_CUTOFF_INVENTED')
        require(clock['first_public_time_proven'] is False and clock['exact_historical_text_availability_proven'] is False, 'HISTORICAL_AVAILABILITY_INFERRED')
        require(clock['rule'] == d['decision_and_label']['recommended_decision'], 'CLOCK_PATHS_DISAGREE')
        require('strictly later than the official SEC filing date' in clock['rule'], 'CLOCK_LAG_CHANGED')
        require(r['horizon_mask_choice']['selected'] == 'SEPARATE_HORIZON_SPECIFIC_MASKS_COMMON_TO_F0_F1_F2', 'HORIZONS_COLLAPSED')
        require(d['prediction_arms']['common_sample_and_label'] is True and d['prediction_arms']['common_sample_scope'] == 'WITHIN_EACH_HORIZON_ACROSS_F0_F1_F2_NOT_ACROSS_HORIZONS', 'ARM_MASK_SCOPE_CHANGED')
        require(d['decision_and_label']['primary_horizon_subsequent_sessions'] == 5 and d['decision_and_label']['alternative_horizon_subsequent_sessions'] == 21, 'HORIZONS_CHANGED')
        require('d-20 through d+h inclusive' in r['required_price_window'], 'WINDOW_SHORTENED')
        require(d['sample']['starting_cohort_price_coverage_reselection'] is False, 'FUTURE_PRICE_COHORT_RESELECTION')
        require('unscored with reason' in d['decision_and_label']['missing_or_delisted_label_policy'] and 'not full-cohort performance' in d['decision_and_label']['missing_or_delisted_label_policy'], 'CONDITIONAL_ESTIMAND_LOST')
        require('no automatic DlyPrc quote fallback' in d['decision_and_label']['close_availability_rule'], 'QUOTE_FALLBACK_ALLOWED')
        require(d['current_engineering_evidence']['formal_cohort_count'] is None and d['current_engineering_evidence']['admitted_financial_observations'] is None, 'FORMAL_COUNTS_INFERRED')
        require(r['preparation_code_pattern']['status'] == 'UNACCEPTED_SOURCE_LITERAL_DIAGNOSTIC_NOT_ELIGIBILITY', 'CODE_PATTERN_ACCEPTED_BY_ACCIDENT')
        require(d['prior_accepted_scope']['full_financial_protocol'] is False, 'OLD_ACCEPTANCE_EXPANDED')
    except (KeyError, TypeError):
        raise ContractError('MISSING_OR_WRONG_TYPE_CONTRACT_FIELD') from None

def apply_checked(base_bytes, patch_bytes, source_kind):
    require(source_kind in {'repository', 'review'}, 'UNKNOWN_SOURCE_KIND')
    patch = decode(patch_bytes)
    expected = patch['source_repository_h_sha256'] if source_kind == 'repository' else patch['source_review_h_sha256']
    require(sha(base_bytes) == expected, 'SOURCE_HASH_MISMATCH')
    base = decode(base_bytes)
    result = copy.deepcopy(base)
    for operation in patch['operations']:
        path = operation['path']
        require(path.startswith('/') and 'development_cik_exclusions' not in path, 'FORBIDDEN_PATCH_PATH')
        keys = [x.replace('~1', '/').replace('~0', '~') for x in path[1:].split('/')]
        parent = result
        for k in keys[:-1]:
            require(isinstance(parent, dict) and k in parent, 'PATCH_PARENT_ABSENT')
            parent = parent[k]
        require(isinstance(parent, dict), 'PATCH_PARENT_NOT_OBJECT')
        leaf = keys[-1]
        op = operation['op']
        if op == 'add':
            require(leaf not in parent, 'PATCH_ADD_TARGET_EXISTS')
            parent[leaf] = copy.deepcopy(operation['value'])
        elif op in {'replace', 'remove'}:
            require(leaf in parent and parent[leaf] == operation['expected'], 'PATCH_OLD_VALUE_MISMATCH')
            if op == 'replace':
                parent[leaf] = copy.deepcopy(operation['value'])
            else:
                del parent[leaf]
        else:
            raise ContractError('UNSUPPORTED_PATCH_OPERATION')
    require(result['sample']['development_cik_exclusions'] == base['sample']['development_cik_exclusions'], 'PRIVATE_EXCLUSIONS_CHANGED')
    # Financial numeric/model sections inherited exactly; the new common-mask scope is the only arms addition.
    for key in ['features', 'metrics', 'development_evidence_only']:
        require(result[key] == base[key], 'INHERITED_NUMERIC_CONTRACT_CHANGED')
    arms = copy.deepcopy(result['prediction_arms'])
    arms.pop('common_sample_scope', None)
    require(arms == base['prediction_arms'], 'INHERITED_MODEL_CONTRACT_CHANGED')
    validate(result)
    return result

def compare_review(candidate, review):
    validate(candidate)
    validate(review)
    sanitized = copy.deepcopy(candidate)
    original = sanitized['sample']['development_cik_exclusions']
    replacement = review['sample']['development_cik_exclusions']
    require(len(original) == len(replacement) == 5, 'EXCLUSION_COUNT_CHANGED')
    require(replacement == [f'REDACTED_DEVELOPMENT_CIK_{k}' for k in range(1, 6)], 'WRONG_REDACTION_SET')
    sanitized['sample']['development_cik_exclusions'] = replacement
    require(sanitized == review, 'REVIEW_SEMANTIC_MISMATCH')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    a = commands.add_parser('apply')
    a.add_argument('--source-kind', choices=['repository', 'review'], required=True)
    a.add_argument('--base', type=Path, required=True)
    a.add_argument('--patch', type=Path, required=True)
    a.add_argument('--output', type=Path, required=True)
    c = commands.add_parser('check')
    c.add_argument('--candidate', type=Path, required=True)
    r = commands.add_parser('compare-review')
    r.add_argument('--candidate', type=Path, required=True)
    r.add_argument('--review', type=Path, required=True)
    args = p.parse_args()
    try:
        if args.command == 'apply':
            require(not args.output.exists(), 'OUTPUT_ALREADY_EXISTS')
            candidate = apply_checked(args.base.read_bytes(), args.patch.read_bytes(), args.source_kind)
            output = (json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('xb') as file:
                file.write(output)
            print(json.dumps({'status': 'DRAFT_WRITTEN_NOT_ACCEPTED', 'sha256': sha(output), 'source_kind': args.source_kind, 'financial_execution': False}))
        elif args.command == 'check':
            b = args.candidate.read_bytes()
            validate(decode(b))
            print(json.dumps({'status': 'PASS_STATIC_CONTRACT_ONLY', 'sha256': sha(b), 'financial_execution': False}))
        else:
            compare_review(decode(args.candidate.read_bytes()), decode(args.review.read_bytes()))
            print(json.dumps({'status': 'PASS_AFTER_DECLARED_CIK_REDACTION_ONLY', 'financial_execution': False}))
    except ContractError as error:
        print(json.dumps({'status': 'REFUSED', 'reason': str(error)}))
        raise SystemExit(2) from None
    except OSError:
        print(json.dumps({'status': 'REFUSED', 'reason': 'FILESYSTEM_IO_ERROR'}))
        raise SystemExit(2) from None

if __name__ == '__main__':
    main()
