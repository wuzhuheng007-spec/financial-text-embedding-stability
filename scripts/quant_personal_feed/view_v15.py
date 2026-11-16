"""Build owner-only tech feed, Top 10 and descriptive news-tone windows."""
from __future__ import annotations

import json
import hashlib
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .relevance_v15 import rank_reason, screen


def _time(value):
    try:
        result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError,TypeError):
        return None


def enrich(data:dict, *, owner_id:int, analysis_db:Path, config:dict) -> dict:
    jobs={}
    approved={}
    if analysis_db.is_file():
        with sqlite3.connect(analysis_db.resolve().as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            has_reviews=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analysis_reviews'").fetchone())
            review_join='LEFT JOIN analysis_reviews r ON r.owner_id=j.owner_id AND r.item_key=j.item_key AND r.version=j.version' if has_reviews else ''
            review_column='r.decision AS review_decision,r.response_sha256 AS review_response_sha256' if has_reviews else 'NULL AS review_decision,NULL AS review_response_sha256'
            jobs={(r['item_key'],r['version']):dict(r) for r in db.execute(f'''SELECT j.item_key,j.version,
              status,result_json,validation_json,model_revision,prompt_version,completed_at,
              input_sha256,error_class,{review_column} FROM analysis_jobs j {review_join} WHERE j.owner_id=?''',(owner_id,))}
    approved_db=analysis_db.parent/'personal_analysis_v15d_approved.sqlite3'
    if approved_db.is_file():
        try:
            with sqlite3.connect(approved_db.resolve().as_uri()+'?mode=ro',uri=True) as db:
                db.row_factory=sqlite3.Row
                approved={(r['item_key'],r['version']):dict(r) for r in db.execute('''SELECT
                  item_key,version,content_sha256,display_json,model_revision,prompt_version,
                  reviewed_at_utc FROM reviewed_cards WHERE owner_id=? AND review_decision='ACCEPT'
                  AND prompt_version='personal_tech_news_analysis_v15d_1' ''',(owner_id,))}
        except sqlite3.DatabaseError:
            # A failed analysis cache must not take down market/news display.
            approved={}
    included=[]
    for item in data.get('news',[]):
        label=item.get('screening')
        decision=screen(item)
        # Stored decision is authoritative for the frozen version; missing old
        # labels are screened on read but never falsely called newly ingested.
        if label and label['status']!='INCLUDE':
            continue
        if not label and decision.status!='INCLUDE':
            continue
        job=jobs.get((item['item_key'],item['version']))
        item['screening_v15']=decision.as_dict() | (label or {})
        item['screening_v15']['event_group_id']=decision.event_group_id
        item['screening_v15']['entity_ids']=list(decision.entity_ids)
        raw_status=job['status'] if job else 'NOT_QUEUED'
        review_decision=job['review_decision'] if job else None
        if job and job['result_json'] and job['review_response_sha256'] != hashlib.sha256(job['result_json'].encode('utf-8')).hexdigest():
            review_decision=None
        item['analysis_status']=('COMPLETE' if review_decision=='ACCEPT' else
            'REVIEW_REJECTED' if review_decision=='REJECT' else 'REVIEW_PENDING') if raw_status=='COMPLETE' else raw_status
        item['analysis_review_decision']=review_decision
        item['analysis_model_revision']=job['model_revision'] if job else None
        item['analysis_prompt_version']=job['prompt_version'] if job else None
        item['analysis_completed_at']=job['completed_at'] if job else None
        item['analysis_error_class']=job['error_class'] if job else None
        item['analysis']=json.loads(job['result_json']) if job and job['result_json'] and review_decision=='ACCEPT' else None
        item['analysis_validation']=json.loads(job['validation_json']) if job and job['validation_json'] else None
        new_card=approved.get((item['item_key'],item['version']))
        if new_card and new_card['content_sha256']==item.get('content_sha256') and new_card['display_json']:
            try:
                display=json.loads(new_card['display_json'])
            except json.JSONDecodeError:
                display=None
            if isinstance(display,dict):
                item['analysis_status']='COMPLETE'
                item['analysis_review_decision']='ACCEPT'
                item['analysis_model_revision']=new_card['model_revision']
                item['analysis_prompt_version']=new_card['prompt_version']
                item['analysis_completed_at']=new_card['reviewed_at_utc']
                item['analysis_error_class']=None
                item['analysis']=display
                item['analysis_validation']={'valid':True,'semantic_status':'CODEX_REVIEW_ACCEPTED',
                                             'supplier_claim_verified':False}
        item['product_rank']=rank_reason(decision,published_at_utc=item.get('published_at_utc'),
            received_at=item['received_at'],source_url=item.get('source_url'),version=item['version'],
            weights=config['top_weights'])
        included.append(item)
    data['news']=included
    by_group={}
    for item in sorted(included,key=lambda x:(-x['product_rank']['score'],x['item_key'])):
        group=item['screening_v15'].get('event_group_id') or item['item_key']
        by_group.setdefault(group,item)
    data['top10']=[{'item_key':x['item_key'],'version':x['version'],'event_group_id':group,
                    'title':x.get('display_title') or x.get('title') or '原文未提供标题',
                    'title_kind':x.get('display_title_kind'),
                    'headline_zh':x['analysis'].get('headline_zh') if x['analysis'] else None,
                    'headline_en':x['analysis'].get('headline_en') if x['analysis'] else None,
                    'published_at_utc':x.get('published_at_utc'),'received_at':x['received_at'],
                    'analysis_status':x['analysis_status'],
                    'tone':x['analysis'].get('tone') if x['analysis'] else 'UNKNOWN',
                    'scope':x['screening_v15'].get('scope'),
                    'claim_stage':x['screening_v15'].get('claim_stage'),
                    'source_status':'SUPPLIER_REPORT_UNVERIFIED',
                    'evidence_phrase':x['screening_v15'].get('evidence_phrase'),
                    'entities':x['screening_v15'].get('entity_ids') or [],
                    'rank':x['product_rank']} for group,x in list(by_group.items())[:config['top_limit']]]
    now=datetime.now(timezone.utc)
    windows=[]
    for hours in config['sentiment_windows_hours']:
        counted=set(); tones=Counter(); pending=0
        for item in included:
            timestamp=_time(item.get('published_at_utc')) or _time(item.get('received_at'))
            if timestamp is None or (now-timestamp).total_seconds()>hours*3600:
                continue
            group=item['screening_v15'].get('event_group_id') or item['item_key']
            if group in counted: continue
            counted.add(group)
            if item['analysis_status']=='COMPLETE': tones[item['analysis']['tone']]+=1
            else: pending+=1
        windows.append({'hours':hours,'event_count':len(counted),'unanalysed_count':pending,
                        'insufficient':len(counted)<config['sentiment_min_events'],
                        'news_tone_counts':dict(tones),'observed_market_change':None})
    data['sentiment_observation']=windows
    data['news_mode']='TECH_MACRO'
    data['analysis_worker_state']='NOT_STARTED'
    if analysis_db.is_file():
        with sqlite3.connect(analysis_db.resolve().as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute('SELECT state,heartbeat_at,pid FROM analysis_worker_state WHERE owner_id=?',(owner_id,)).fetchone()
            if row:
                # Generation runs in a child process and can legitimately take
                # the full configured load plus generation time between heartbeats.
                worker_cfg=config.get('analysis_worker', {})
                one_attempt=int(worker_cfg.get('model_load_timeout_seconds',240)) + int(worker_cfg.get('generation_timeout_seconds',180))
                stale_after=max(90, one_attempt * (1 + int(worker_cfg.get('repair_attempts',1))) + 30)
                heart=_time(row[1]); stale=not heart or (now-heart).total_seconds()>stale_after
                data['analysis_worker_state']='DISCONNECTED' if stale else row[0]
            data['analysis_queue_count']=db.execute("SELECT COUNT(*) FROM analysis_jobs WHERE owner_id=? AND status='PENDING'",(owner_id,)).fetchone()[0]
    return data
