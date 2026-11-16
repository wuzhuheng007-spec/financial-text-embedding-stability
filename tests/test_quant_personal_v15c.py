"""Targeted mechanical checks for evidence-ID bilingual analysis."""
import json

from scripts.quant_personal_feed.analysis_contract_v15c import (
    build_spans, parse_and_validate, validate,
)


def _object(ids=None):
    return {'headline_zh': '虚构公司扩建工厂',
            'headline_en': 'Fictional firm expands a plant',
            'reported_zh': '虚构公司称拟扩建工厂。',
            'reported_en': 'Fictional firm said it plans to expand a plant.',
            'analysis_zh': '若实施，设备需求可能变化；需看后续公告。',
            'analysis_en': 'If carried out, equipment demand may change; watch for an announcement.',
            'tone': 'NEUTRAL', 'evidence_ids': ids or ['S1'], 'impacts': []}


def test_source_spans_are_exact_and_ordered():
    spans, meta = build_spans('虚构公司扩建工厂', '虚构公司称拟扩建工厂。另称资金未落实。')
    assert [x['text'] for x in spans] == [
        '虚构公司扩建工厂', '虚构公司称拟扩建工厂。', '另称资金未落实。']
    assert meta['omitted_spans'] == 0


def test_evidence_ids_resolve_to_exact_input_spans():
    spans, _ = build_spans('虚构公司称拟扩建工厂。', '')
    obj, report, mode, _ = parse_and_validate(json.dumps(_object(), ensure_ascii=False), spans)
    assert mode == 'RAW_JSON' and obj['evidence_ids'] == ['S1']
    assert report['valid'] and report['evidence'][0]['quote'] == spans[0]['text']


def test_hallucinated_or_duplicate_id_fails():
    spans, _ = build_spans('虚构公司称拟扩建工厂。', '')
    assert 'EVIDENCE_ID_UNKNOWN' in validate(_object(['S9']), spans)['errors']
    assert 'EVIDENCE_IDS_SHAPE' in validate(_object(['S1', 'S1']), spans)['errors']


def test_extra_number_fails_without_counting_source_id():
    spans, _ = build_spans('虚构公司称拟扩建工厂。', '')
    assert validate(_object(), spans)['valid']
    obj = _object(); obj['reported_en'] += ' 500 plants.'
    assert 'NUMERIC_TOKEN_NOT_IN_INPUT' in validate(obj, spans)['errors']


def test_complete_sentence_budget_never_truncates_evidence():
    spans, meta = build_spans(None, '甲公司投资100万元。乙公司投资200万元。',
                              max_source_characters=len('甲公司投资100万元。'))
    assert len(spans) == 1 and meta['omitted_spans'] == 1
    assert spans[0]['text'] == '甲公司投资100万元。'
