"""Speaker lock must stop name guessing while retaining source evidence."""
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.quant_personal_feed.analysis_contract_v15c import build_spans
from scripts.quant_personal_feed.analysis_contract_v15d import (
    mask_speakers, parse_and_validate,
)
from scripts.quant_personal_feed.view_v15 import enrich
from scripts.quant_personal_feed.review_v15d import apply_reviews, object_hash


def _obj(name='PERSON_A'):
    return {'headline_zh': f'委员{name}谈利率',
            'headline_en': f'Committee member {name} discusses rates',
            'reported_zh': f'委员{name}称应评估情况。',
            'reported_en': f'Committee member {name} said conditions should be assessed.',
            'analysis_zh': '对科技公司的影响未知，需看正式政策。',
            'analysis_en': 'The effect on technology companies is unknown; watch for a formal policy.',
            'tone': 'UNKNOWN', 'evidence_ids': ['S1'], 'impacts': []}


def test_role_speaker_mask_and_exact_restoration():
    original, _ = build_spans('日本央行审议委员高田创：应评估情况。', '')
    masked, mapping = mask_speakers(original)
    assert mapping == {'PERSON_A': '高田创'}
    assert '高田创' not in masked[0]['text']
    raw, display, check, _, _ = parse_and_validate(json.dumps(_obj(), ensure_ascii=False),
                                                    masked, original, mapping)
    assert check['valid'] and raw['headline_en'].endswith('discusses rates')
    assert '高田创' in display['reported_en']
    assert check['evidence'][0]['quote'] == original[0]['text']


def test_latinized_substitution_is_rejected():
    original, _ = build_spans('日本央行审议委员高田创：应评估情况。', '')
    masked, mapping = mask_speakers(original)
    _, _, check, _, _ = parse_and_validate(json.dumps(_obj('Invented Name'), ensure_ascii=False),
                                           masked, original, mapping)
    assert 'LOCKED_NAME_NOT_PRESERVED' in check['errors']


def test_committee_word_is_not_person():
    original, _ = build_spans('委员会表示：应评估情况。', '')
    _, mapping = mask_speakers(original)
    assert mapping == {}


def test_approved_card_requires_same_owner_and_source_version(tmp_path):
    db_path=tmp_path/'personal_analysis_v15d_approved.sqlite3'
    with sqlite3.connect(db_path) as db:
        db.execute('''CREATE TABLE reviewed_cards(owner_id INTEGER,item_key TEXT,version INTEGER,
          content_sha256 TEXT,display_json TEXT,model_revision TEXT,prompt_version TEXT,
          reviewed_at_utc TEXT,review_decision TEXT)''')
        db.execute('''INSERT INTO reviewed_cards VALUES(1,?,1,?,?,'rev',
          'personal_tech_news_analysis_v15d_1','2026-11-16T00:00:00Z','ACCEPT')''',
          ('a'*64,'b'*64,json.dumps(_obj(),ensure_ascii=False)))
    cfg={'top_limit':10,'top_weights':{'direct_subject':45,'industry':24,
         'macro':28,'material_category':18,'source_url':4,'correction':8,'denial':5},
         'sentiment_windows_hours':[1,6,24],'sentiment_min_events':2}
    item={'item_key':'a'*64,'version':1,'title':'NVIDIA 公布业绩','content':'报告内容',
          'content_sha256':'b'*64,'received_at':'2026-11-16T00:00:00Z',
          'published_at_utc':'2026-11-16T00:00:00Z'}
    old_db=tmp_path/'absent.sqlite3'
    accepted=enrich({'news':[item.copy()]},owner_id=1,analysis_db=old_db,config=cfg)
    assert accepted['news'][0]['analysis_status']=='COMPLETE'
    assert accepted['news'][0]['analysis']['headline_en']==_obj()['headline_en']
    other=enrich({'news':[item.copy()]},owner_id=2,analysis_db=old_db,config=cfg)
    assert other['news'][0]['analysis'] is None
    changed=dict(item,content_sha256='c'*64)
    mismatch=enrich({'news':[changed]},owner_id=1,analysis_db=old_db,config=cfg)
    assert mismatch['news'][0]['analysis'] is None


def test_review_import_requires_complete_english_and_bound_hash(tmp_path:Path):
    private=tmp_path/'private'; private.mkdir()
    source_db=tmp_path/'source.sqlite3'
    with sqlite3.connect(source_db) as db:
        db.execute('CREATE TABLE news_versions(owner_id INTEGER,item_key TEXT,version INTEGER,content_sha256 TEXT)')
        db.execute('INSERT INTO news_versions VALUES(1,?,1,?)',('a'*64,'b'*64))
    manifest={'items':[{'index':1,'item_key':'a'*64,'version':1,'content_sha256':'b'*64}]}
    (private/'input_manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    raw=_obj(); display=_obj('高田创')
    result={'index':1,'item_key':'a'*64,'version':1,'content_sha256':'b'*64,
            'status':'MECHANICAL_COMPLETE','model_revision':'rev',
            'prompt_version':'personal_tech_news_analysis_v15d_1',
            'selected':{'object':display,'raw_object':raw,
                        'evidence':[{'id':'S1','quote':'委员高田创称应评估情况。'}]}}
    (private/'item_01_result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    review={'review_version':'CODEX_REVIEW_V15D_1','items':[{'index':1,
            'decision':'ACCEPT','reason_code':'SOURCE_SUPPORTED',
            'display_object_sha256':object_hash(display)}]}
    review_path=tmp_path/'review.json';review_path.write_text(json.dumps(review),encoding='utf-8')
    db_path=tmp_path/'approved.sqlite3'
    assert apply_reviews(review_path,private_dir=private,approved_db=db_path,
                         source_db=source_db,owner=1,
                         receipt_path=tmp_path/'receipt.json')['decisions']['ACCEPT']==1
    result['selected']['raw_object']['reported_en']='Japanese央行 member PERSON_A'
    (private/'item_01_result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    with pytest.raises(ValueError,match='untranslated Han'):
        apply_reviews(review_path,private_dir=private,approved_db=tmp_path/'second.sqlite3',
                      source_db=source_db,owner=1,receipt_path=tmp_path/'other.json')


def test_corrupt_approval_cache_does_not_hide_news(tmp_path):
    (tmp_path/'personal_analysis_v15d_approved.sqlite3').write_bytes(b'not a database')
    cfg={'top_limit':10,'top_weights':{'direct_subject':45,'industry':24,
         'macro':28,'material_category':18,'source_url':4,'correction':8,'denial':5},
         'sentiment_windows_hours':[1,6,24],'sentiment_min_events':2}
    item={'item_key':'a'*64,'version':1,'title':'NVIDIA 公布业绩','content':'报告内容',
          'received_at':'2026-11-16T00:00:00Z','published_at_utc':'2026-11-16T00:00:00Z'}
    result=enrich({'news':[item]},owner_id=1,analysis_db=tmp_path/'absent.sqlite3',config=cfg)
    assert len(result['news'])==1 and result['news'][0]['analysis'] is None
