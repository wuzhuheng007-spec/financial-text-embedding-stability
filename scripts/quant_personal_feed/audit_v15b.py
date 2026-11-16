"""Summarize the frozen private development batch without source text or outputs."""
from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .analysis_v15b import ANALYSIS, CONFIG, ROOT

BATCH = ROOT / 'apps/quant_portal/var/v15_20261116T0431Z/analysis_batch_private.json'
PUBLIC = ROOT / 'results/quant_portal_personal_v15_20261116/model_run_redacted.json'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(batch_path: Path = BATCH, db_path: Path = ANALYSIS) -> dict:
    batch=json.loads(batch_path.read_text(encoding='utf-8'))
    keys={(x['item_key'],x['version']) for x in batch['items']}
    if len(keys)!=len(batch['items']): raise ValueError('Duplicate frozen entries')
    status=collections.Counter(); errors=collections.Counter(); modes=collections.Counter()
    validation_errors=collections.Counter(); repairs=0; actual_attempts=0
    review=collections.Counter(); earliest=None; latest=None
    with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        reviews_exist=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analysis_reviews'").fetchone())
        for key,version in keys:
            job=db.execute('SELECT status,result_json,prompt_version,model_revision FROM analysis_jobs WHERE item_key=? AND version=?',(key,version)).fetchone()
            if not job: status['NOT_QUEUED']+=1; continue
            status[job['status']]+=1
            attempts=db.execute('SELECT attempt,started_at,ended_at,error_class,parse_mode,validation_json FROM analysis_attempts WHERE item_key=? AND version=? ORDER BY attempt',(key,version)).fetchall()
            actual_attempts+=len(attempts)
            if len(attempts)>1: repairs+=1
            for attempt in attempts:
                errors[attempt['error_class'] or 'NONE']+=1
                modes[attempt['parse_mode'] or 'NO_PARSE']+=1
                for field in ('started_at','ended_at'):
                    value=attempt[field]
                    if value:
                        earliest=value if earliest is None or value<earliest else earliest
                        latest=value if latest is None or value>latest else latest
                if attempt['validation_json']:
                    for error in json.loads(attempt['validation_json']).get('errors',[]):
                        validation_errors[error]+=1
            if job['status']=='COMPLETE':
                decision='UNREVIEWED'
                if reviews_exist:
                    row=db.execute('SELECT decision,response_sha256 FROM analysis_reviews WHERE item_key=? AND version=?',(key,version)).fetchone()
                    if row and row['response_sha256']==hashlib.sha256(job['result_json'].encode('utf-8')).hexdigest():
                        decision=row['decision']
                review[decision]+=1
    cfg=json.loads(CONFIG.read_text(encoding='utf-8'))
    return {'schema':'personal_demo_v15b_frozen_batch_redacted','created_at_utc':datetime.now(timezone.utc).isoformat(),
            'frozen_batch_sha256':sha(batch_path),'input_snapshot_sha256':batch['input_snapshot_sha256'],
            'config_sha256':sha(CONFIG),'model_revision':cfg['model']['revision'],
            'model_config_sha256':cfg['model']['config_sha256'],'tokenizer_sha256':cfg['model']['tokenizer_sha256'],
            'worker_source_sha256_at_audit':sha(ROOT/'scripts/quant_personal_feed/analysis_v15b.py'),
            'contract_source_sha256_at_audit':sha(ROOT/'scripts/quant_personal_feed/analysis_contract_v15b.py'),
            'generator_source_sha256_at_audit':sha(ROOT/'scripts/quant_personal_feed/generate_v15b.py'),
            'runtime_source_hash_at_launch':'NOT_SEPARATELY_CAPTURED',
            'planned_items':len(keys),'job_status':dict(sorted(status.items())),
            'generation_attempts':actual_attempts,'items_with_repair':repairs,
            'attempt_error_class':dict(sorted(errors.items())),'parse_mode':dict(sorted(modes.items())),
            'validation_errors':dict(sorted(validation_errors.items())),
            'content_review_decisions':dict(sorted(review.items())),
            'first_attempt_at_utc':earliest,'last_attempt_at_utc':latest,
            'scope':'REPEATED_DEVELOPMENT_NEWS_ONLY_NO_INDEPENDENT_ACCURACY_CLAIM'}


if __name__=='__main__':
    PUBLIC.parent.mkdir(parents=True,exist_ok=True)
    PUBLIC.write_text(json.dumps(summarize(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(PUBLIC)
