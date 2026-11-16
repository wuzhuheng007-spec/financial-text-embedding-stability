"""Import response-hash-bound same-team reviews into an owner-only cache.

The importer never claims that a supplier report was independently verified.
Raw source and model output remain in ignored local paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .analysis_v15b import FEED, ROOT, owner_id
from .run_v15d_batch import PRIVATE, PUBLIC, write_json

APPROVED = ROOT / 'apps/quant_portal/var/personal_analysis_v15d_approved.sqlite3'
DECISIONS = {'ACCEPT', 'REJECT', 'UNRESOLVED'}


def object_hash(obj: dict) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')).encode('utf-8')).hexdigest()


def apply_reviews(review_path: Path, *, private_dir: Path = PRIVATE,
                  approved_db: Path = APPROVED, source_db: Path = FEED,
                  owner: int | None = None, receipt_path: Path | None = None) -> dict:
    owner = owner if owner is not None else owner_id()
    payload = json.loads(review_path.read_text(encoding='utf-8'))
    if payload.get('review_version') != 'CODEX_REVIEW_V15D_1':
        raise ValueError('Review version mismatch')
    reviews = payload.get('items')
    if not isinstance(reviews, list) or len({x['index'] for x in reviews}) != len(reviews):
        raise ValueError('Duplicate or invalid review rows')
    manifest = json.loads((private_dir/'input_manifest.json').read_text(encoding='utf-8'))
    indexed = {x['index']: x for x in manifest['items']}
    counts = {x: 0 for x in DECISIONS}
    approved_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_db.resolve().as_uri()+'?mode=ro', uri=True) as source, \
         sqlite3.connect(approved_db) as target:
        target.execute('''CREATE TABLE IF NOT EXISTS reviewed_cards (
          owner_id INTEGER NOT NULL,item_key TEXT NOT NULL,version INTEGER NOT NULL,
          content_sha256 TEXT NOT NULL,display_json TEXT,model_revision TEXT NOT NULL,
          prompt_version TEXT NOT NULL,response_sha256 TEXT NOT NULL,
          review_decision TEXT NOT NULL,review_reason TEXT NOT NULL,
          review_version TEXT NOT NULL,reviewed_at_utc TEXT NOT NULL,
          PRIMARY KEY(owner_id,item_key,version,prompt_version))''')
        for review in reviews:
            i = review['index']
            if i not in indexed or review['decision'] not in DECISIONS:
                raise ValueError('Review target or decision invalid')
            item = indexed[i]
            result = json.loads((private_dir/f'item_{i:02d}_result.json').read_text(encoding='utf-8'))
            if result['status'] != 'MECHANICAL_COMPLETE' or not result['selected']:
                raise ValueError('Review target is not mechanically complete')
            if result['item_key'] != item['item_key'] or result['content_sha256'] != item['content_sha256']:
                raise ValueError('Frozen input mismatch')
            current = source.execute('''SELECT content_sha256 FROM news_versions
              WHERE owner_id=? AND item_key=? AND version=?''',
              (owner, item['item_key'], item['version'])).fetchone()
            if not current or current[0] != item['content_sha256']:
                raise ValueError('Current source version mismatch')
            selected = result['selected']
            digest = object_hash(selected['object'])
            if review['display_object_sha256'] != digest:
                raise ValueError('Review response hash mismatch')
            if review['decision'] == 'ACCEPT' and not selected['evidence']:
                raise ValueError('Accepted card needs source evidence')
            if review['decision'] == 'ACCEPT':
                raw_obj = selected['raw_object']
                english = [raw_obj.get(k, '') for k in
                           ('headline_en', 'reported_en', 'analysis_en')]
                english.extend(x.get('why_en', '') for x in raw_obj.get('impacts', [])
                               if isinstance(x, dict))
                if any(re.search(r'[\u4e00-\u9fff]', str(value)) for value in english):
                    raise ValueError('English field contains untranslated Han text')
            obj = dict(selected['object'])
            obj['evidence'] = selected['evidence']
            obj['quote'] = selected['evidence'][0]['quote'] if selected['evidence'] else None
            obj['source_status'] = 'SUPPLIER_REPORT_UNVERIFIED'
            existing = target.execute('''SELECT response_sha256,review_decision FROM reviewed_cards WHERE
              owner_id=? AND item_key=? AND version=? AND prompt_version=?''',
              (owner,item['item_key'],item['version'],result['prompt_version'])).fetchone()
            if existing and existing[0] != digest:
                raise ValueError('Review would overwrite a different response')
            if existing and existing[1] != review['decision']:
                raise ValueError('Review decision change requires a new review version')
            target.execute('''INSERT INTO reviewed_cards(owner_id,item_key,version,
              content_sha256,display_json,model_revision,prompt_version,response_sha256,
              review_decision,review_reason,review_version,reviewed_at_utc)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,item_key,version,prompt_version)
              DO UPDATE SET review_decision=excluded.review_decision,
                review_reason=excluded.review_reason,review_version=excluded.review_version,
                reviewed_at_utc=excluded.reviewed_at_utc''',
              (owner,item['item_key'],item['version'],item['content_sha256'],
               json.dumps(obj,ensure_ascii=False) if review['decision']=='ACCEPT' else None,
               result['model_revision'],result['prompt_version'],digest,
               review['decision'],review['reason_code'],payload['review_version'],
               datetime.now(timezone.utc).isoformat()))
            counts[review['decision']] += 1
    summary = {'schema':'v15d_semantic_review_redacted','review_version':payload['review_version'],
               'private_review_sha256':hashlib.sha256(review_path.read_bytes()).hexdigest(),
               'reviewed':len(reviews),'decisions':counts,
               'scope':'CODEX_REVIEW_SAME_TEAM_DEVELOPMENT_NOT_INDEPENDENT_GOLD',
               'supplier_claims_independently_verified':False}
    write_json(receipt_path or PUBLIC/'semantic_review_redacted.json',summary)
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('private_review_json',type=Path)
    args=parser.parse_args()
    print(json.dumps(apply_reviews(args.private_review_json),ensure_ascii=False))
