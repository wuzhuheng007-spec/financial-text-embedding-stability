"""Redacted read-only receipt after v1.5d reviewed cards are loaded."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .analysis_v15b import owner_id
from .review_v15d import APPROVED
from .run_v15d_batch import PUBLIC, write_json
from .runtime_v15 import summarize


def receipt() -> dict:
    state = summarize()
    reviewed = {}
    if APPROVED.is_file():
        with sqlite3.connect(APPROVED.resolve().as_uri()+'?mode=ro',uri=True) as db:
            reviewed = {r[0]:r[1] for r in db.execute('''SELECT review_decision,COUNT(*)
               FROM reviewed_cards WHERE owner_id=? GROUP BY review_decision''',(owner_id(),))}
    return {'schema':'v15d_runtime_redacted',
            'observed_at_utc':datetime.now(timezone.utc).isoformat(),
            'portal_ready_http':state['portal_ready_http'],
            'quote_symbols':state['quote_symbols'],
            'quote_count':state['quote_count'],
            'included_news_in_current_read':state['included_news_in_current_read'],
            'top10_positions':state['top10_positions'],
            'accepted_model_cards_visible':state['accepted_model_cards_visible'],
            'analysis_display_status':state['analysis_display_status'],
            'approved_cache_review_decisions':reviewed,
            'source_states':state['source_states'],
            'supplier_claims_independently_verified':False,
            'browser_visual_acceptance':'NOT_RUN'}


if __name__ == '__main__':
    result=receipt()
    write_json(PUBLIC/'runtime_redacted.json',result)
    print(json.dumps({k:result[k] for k in ('portal_ready_http','quote_count',
         'top10_positions','accepted_model_cards_visible','approved_cache_review_decisions')}))
