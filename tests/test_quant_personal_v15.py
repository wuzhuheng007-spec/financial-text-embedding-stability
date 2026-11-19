"""Risks at the personal v1.5 ingestion and analysis boundary."""
import json
import hashlib
import sqlite3
import sqlite3
from pathlib import Path

from scripts.quant_personal_feed.analysis_contract_v15 import parse_response, validate
from scripts.quant_personal_feed.relevance_v15 import screen
from scripts.quant_personal_feed.store import PersonalFeedStore
from scripts.quant_personal_feed.analysis_contract_v15b import parse_response as parse_b, validate as validate_b
from scripts.quant_personal_feed.view_v15 import enrich
from scripts.quant_personal_feed import analysis_v15b
from scripts.quant_personal_feed import review_v15b
from datetime import datetime, timezone


def test_screen_direct_macro_and_unrelated():
    assert screen({'title':'NVIDIA 发布季度财报', 'content':'披露营收'}).status == 'INCLUDE'
    assert screen({'content':'美国CPI数据公布，通胀上升'}).status == 'INCLUDE'
    assert screen({'title':'地方体育联赛开赛', 'content':'参赛名单公布'}).status == 'EXCLUDE'
    assert screen({'content':'普通企业宣布与苹果水果经销商合作'}).status != 'INCLUDE'


def test_excluded_text_is_not_saved(tmp_path:Path):
    store=PersonalFeedStore(tmp_path/'private.sqlite3',1)
    item={'title':'体育活动','content':'私人不相关正文'}
    dec=screen(item).as_dict()
    store.record_news_screening(1,item,dec,received_at='2026-11-16T00:00:00Z')
    assert store.db.execute('SELECT COUNT(*) FROM news_items').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM news_screening').fetchone()[0] == 1
    assert store.db.execute('SELECT COUNT(*) FROM news_screening_pending').fetchone()[0] == 0
    text=store.db.execute('SELECT reason_key FROM news_screening').fetchone()[0]
    assert '私人不相关正文' not in text
    store.close()


def test_no_fake_numeric_or_quote():
    obj={k:None for k in ('first_order_zh','first_order_en','conditional_chain_zh','conditional_chain_en',
         'conditions_zh','conditions_en','counter_zh','counter_en','watch_zh','watch_en','unknown_zh','unknown_en')}
    obj.update(headline_zh='测试',headline_en='Test',summary_zh='增长12%',summary_en='up 12%',
               tone='UNKNOWN',facts=[{'zh':'增长12%','en':'up 12%','quote':'增长12%'}],targets=[])
    assert validate(obj,'公司增长12%')['valid']
    obj['summary_en']='up 99%'
    assert 'NUMERIC_TOKEN_NOT_IN_INPUT' in validate(obj,'公司增长12%')['errors']
    obj['summary_en']='up 12%'; obj['facts'][0]['quote']='not in source'
    assert 'QUOTE_NOT_FOUND_FACT_0' in validate(obj,'公司增长12%')['errors']


def test_strict_json_and_single_fence_only():
    assert parse_response('{"facts":[]}')[1]=='RAW_JSON'
    assert parse_response('```json\n{"facts":[]}\n```')[1]=='FENCE_ADAPTED'
    for bad in ('{"a":1,"a":2}', '{"a":NaN}', 'text {"a":1}', '{"a":1}{"b":2}'):
        try: parse_response(bad)
        except (ValueError,json.JSONDecodeError): pass
        else: assert False,bad


def test_compact_analysis_rejects_paraphrased_quote_and_invented_number():
    obj={"headline_zh":"测试","headline_en":"Test","reported_zh":"公司公告","reported_en":"Company report",
         "analysis_zh":"取决于执行","analysis_en":"Depends on execution","tone":"UNKNOWN",
         "quote":"公司公告","impacts":[]}
    assert validate_b(obj,"公司公告")['valid']
    obj['quote']='改写引文'
    assert 'QUOTE_NOT_CONTIGUOUS' in validate_b(obj,"公司公告")['errors']
    obj['quote']='公司公告';obj['analysis_en']='Target 99%'
    assert 'NUMERIC_TOKEN_NOT_IN_INPUT' in validate_b(obj,"公司公告")['errors']
    assert parse_b('```json\n'+json.dumps(obj,ensure_ascii=False)+'\n```')[1]=='FENCE_ADAPTED'
    obj['analysis_en']='buy now'
    assert 'PROHIBITED_ADVICE_OR_PROBABILITY' in validate_b(obj,'公司公告')['errors']


def test_top10_deduplicates_related_reports_without_inventing_sentiment(tmp_path:Path):
    cfg={"top_limit":10,"top_weights":{"direct_subject":45,"industry":24,"macro":28,
         "material_category":18,"source_url":4,"correction":8,"denial":5},
         "sentiment_windows_hours":[1,6,24],"sentiment_min_events":2}
    rows=[]
    for key in ('a'*64,'b'*64):
        item={'item_key':key,'version':1,'title':'NVIDIA 发布季度财报','content':'披露营收',
              'received_at':'2026-11-16T00:00:00Z','published_at_utc':'2026-11-16T00:00:00Z',
              'source_url':None,'screening':None}
        rows.append(item)
    data=enrich({'news':rows},owner_id=1,analysis_db=tmp_path/'absent.sqlite3',config=cfg)
    assert len(data['news'])==2
    assert len(data['top10'])==1
    assert data['top10'][0]['tone']=='UNKNOWN'
    assert data['analysis_worker_state']=='NOT_STARTED'


def test_model_repair_is_logged_once_and_original_response_is_kept(tmp_path:Path,monkeypatch):
    feed=tmp_path/'feed.sqlite3';store=PersonalFeedStore(feed,1)
    item={'title':'NVIDIA 公告','content':'NVIDIA 公告。'}
    key=store.put_news(1,item,received_at='2026-11-16T00:00:00Z',max_items=10,max_versions=2)
    assert key=='NEW'
    decision=screen(item).as_dict();store.record_news_screening(1,item,decision,received_at='2026-11-16T00:00:00Z')
    row=store.db.execute('SELECT item_key,version,content_sha256 FROM news_versions').fetchone()
    store.close()
    monkeypatch.setattr(analysis_v15b,'FEED',feed)
    monkeypatch.setattr(analysis_v15b,'ROOT',tmp_path)
    valid={'headline_zh':'公司公告','headline_en':'Company announcement',
           'reported_zh':'NVIDIA 公告','reported_en':'NVIDIA announced',
           'analysis_zh':'细节未知','analysis_en':'Details unknown','tone':'UNKNOWN',
           'quote':'NVIDIA 公告','impacts':[]}
    responses=iter([{'raw':'not json','generated_tokens':2},
                    {'raw':json.dumps(valid,ensure_ascii=False),'generated_tokens':20}])
    monkeypatch.setattr(analysis_v15b,'_generation',lambda *_: (next(responses),None,0.1))
    db=analysis_v15b.open_db(tmp_path/'analysis.sqlite3')
    db.execute('''INSERT INTO analysis_jobs(owner_id,item_key,version,content_sha256,event_group_id,
      status,priority,enqueued_at,model_revision,prompt_version) VALUES(1,?,?,?,?,?,?,?,? ,?)''',
      (row['item_key'],1,row['content_sha256'],'g','PENDING',1,'2026-11-16T00:00:00Z','rev','prompt'))
    job=db.execute('SELECT * FROM analysis_jobs').fetchone()
    cfg={'analysis_worker':{'max_input_characters':1600,'max_input_tokens':2048,
         'max_new_tokens':768,'seed':1729},'model':{'revision':'rev'}}
    result=analysis_v15b.process_one(db,1,cfg,job)
    assert result['status']=='COMPLETE' and result['attempts']==2
    attempts=db.execute('SELECT attempt,raw_response,error_class FROM analysis_attempts ORDER BY attempt').fetchall()
    assert len(attempts)==2 and attempts[0]['raw_response']=='not json'
    assert attempts[0]['error_class']=='PARSE_JSONDecodeError'
    assert attempts[1]['error_class'] is None
    db.close()


def test_semantic_review_gates_display_and_binds_response_hash(tmp_path:Path,monkeypatch):
    cfg={'top_limit':10,'top_weights':{'direct_subject':45,'industry':24,'macro':28,
         'material_category':18,'source_url':4,'correction':8,'denial':5},
         'sentiment_windows_hours':[1,6,24],'sentiment_min_events':2}
    key='a'*64
    result=json.dumps({'headline_zh':'标题','headline_en':'Headline','tone':'UNKNOWN','quote':'NVIDIA 公布业绩'},ensure_ascii=False)
    db_path=tmp_path/'analysis.sqlite3'
    db=analysis_v15b.open_db(db_path)
    db.execute('''INSERT INTO analysis_jobs(owner_id,item_key,version,content_sha256,event_group_id,
      status,priority,enqueued_at,model_revision,prompt_version,result_json)
      VALUES(1,?,1,?,'g','COMPLETE',1,'2026-11-16T00:00:00Z','rev','prompt',?)''',(key,'b'*64,result))
    db.close()
    item={'item_key':key,'version':1,'title':'NVIDIA 公布业绩','content':'报告内容',
          'content_sha256':'b'*64,'received_at':'2026-11-16T00:00:00Z',
          'published_at_utc':'2026-11-16T00:00:00Z'}
    output=enrich({'news':[item.copy()]},owner_id=1,analysis_db=db_path,config=cfg)
    assert output['news'][0]['analysis_status']=='REVIEW_PENDING'
    assert output['news'][0]['analysis'] is None
    monkeypatch.setattr(review_v15b,'owner_id',lambda:1)
    review_file=tmp_path/'review.json'
    review_file.write_text(json.dumps({'review_version':'CODEX_REVIEW_V15B_1','items':[{
        'item_key':key,'version':1,'result_sha256':hashlib.sha256(result.encode()).hexdigest(),
        'decision':'ACCEPT','reason_code':'SOURCE_SUPPORTED'}]}),encoding='utf-8')
    assert review_v15b.apply_reviews(review_file,db_path)['counts']['ACCEPT']==1
    output=enrich({'news':[item.copy()]},owner_id=1,analysis_db=db_path,config=cfg)
    assert output['news'][0]['analysis_status']=='COMPLETE'
    assert output['news'][0]['analysis']['headline_en']=='Headline'
    changed=dict(item,content_sha256='c'*64)
    stale=enrich({'news':[changed]},owner_id=1,analysis_db=db_path,config=cfg)
    assert stale['news'][0]['analysis_status']=='NOT_QUEUED'
    assert stale['news'][0]['analysis'] is None
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE analysis_jobs SET result_json=? WHERE owner_id=1 AND item_key=?",(result+' ',key))
    output=enrich({'news':[item.copy()]},owner_id=1,analysis_db=db_path,config=cfg)
    assert output['news'][0]['analysis_status']=='REVIEW_PENDING'
    assert output['news'][0]['analysis'] is None
