"""Read-only, aggregate acceptance receipt for the owner-only local portal."""
from __future__ import annotations

import collections
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .analysis_v15b import ANALYSIS, CONFIG, FEED, ROOT, owner_id
from .store import read_personal_cache
from .view_v15 import enrich

OUT=ROOT/'results/quant_portal_personal_v15_20261116/runtime_redacted.json'


def http_code(path:str)->int|None:
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/'+path,timeout=3) as response:
            return response.status
    except Exception:
        return None


def summarize()->dict:
    owner=owner_id()
    cfg=json.loads(CONFIG.read_text(encoding='utf-8'))
    data=read_personal_cache(FEED,owner,news_limit=200,included_only=True)
    data=enrich(data,owner_id=owner,analysis_db=ANALYSIS,config=cfg)
    states={x['provider']:{k:x.get(k) for k in ('state','heartbeat_at','last_success_at','last_push_at')}
            for x in data['states']}
    return {'schema':'personal_demo_v15_runtime_redacted','observed_at_utc':datetime.now(timezone.utc).isoformat(),
            'portal_ready_http':http_code('readyz/'),'portal_i18n_js_http':http_code('static/core/i18n_v15.js'),
            'portal_workspace_js_http':http_code('static/core/workspace_v15.js'),
            'quote_symbols':sorted(x['symbol'] for x in data.get('quotes',[])),
            'quote_count':len(data.get('quotes',[])),
            'included_news_in_current_read':len(data.get('news',[])),
            'top10_positions':len(data.get('top10',[])),
            'analysis_display_status':dict(collections.Counter(x['analysis_status'] for x in data.get('news',[]))),
            'accepted_model_cards_visible':sum(bool(x.get('analysis')) for x in data.get('news',[])),
            'analysis_worker_state':data.get('analysis_worker_state'),
            'analysis_queue_count':data.get('analysis_queue_count'),
            'source_states':states,
            'new_intake_screening_metrics':{k:v for k,v in data.get('news_screening_metrics',{}).items() if k!='owner_id'},
            'legacy_label_counts':data.get('news_screening_counts',{}),
            'supplier_claims_verified':'NO',
            'visual_acceptance':'NOT_RUN_URL_CONFIDENCE_TOOL_STOP'}


if __name__=='__main__':
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(summarize(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(OUT)
