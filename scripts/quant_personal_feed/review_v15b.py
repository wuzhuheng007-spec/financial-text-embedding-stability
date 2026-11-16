"""Apply a private, response-hash-bound content review to local model output.

The input is deliberately kept in ignored app/var; public receipts contain
counts and reason codes only. This is a same-team development review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .analysis_v15b import ANALYSIS, owner_id

DECISIONS = {'ACCEPT', 'REJECT', 'UNRESOLVED'}
REASONS = {'SOURCE_SUPPORTED', 'WRONG_ENTITY', 'WRONG_TRANSLATION',
           'UNSUPPORTED_INFERENCE', 'WRONG_NUMBER', 'WRONG_PERIOD',
           'IRRELEVANT_SCOPE', 'MISSING_QUOTE', 'MIXED_ERRORS', 'UNRESOLVED'}


def apply_reviews(input_path: Path, db_path: Path = ANALYSIS) -> dict:
    payload = json.loads(input_path.read_text(encoding='utf-8'))
    if payload.get('review_version') != 'CODEX_REVIEW_V15B_1':
        raise ValueError('Review version mismatch')
    rows = payload['items']
    if not isinstance(rows, list) or len({(r['item_key'], r['version']) for r in rows}) != len(rows):
        raise ValueError('Duplicate or invalid review rows')
    counts = {x: 0 for x in DECISIONS}
    with sqlite3.connect(db_path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS analysis_reviews(
          owner_id INTEGER NOT NULL,item_key TEXT NOT NULL,version INTEGER NOT NULL,
          response_sha256 TEXT NOT NULL,decision TEXT NOT NULL,reason_code TEXT NOT NULL,
          review_version TEXT NOT NULL,reviewed_at TEXT NOT NULL,
          PRIMARY KEY(owner_id,item_key,version))''')
        for row in rows:
            if row['decision'] not in DECISIONS or row['reason_code'] not in REASONS:
                raise ValueError('Invalid review value')
            saved = db.execute('''SELECT status,result_json FROM analysis_jobs
              WHERE owner_id=? AND item_key=? AND version=?''',
              (owner_id(),row['item_key'],row['version'])).fetchone()
            if not saved or saved[0] != 'COMPLETE' or not saved[1]:
                raise ValueError('Review target lacks completed response')
            if row['decision']=='ACCEPT':
                result=json.loads(saved[1])
                if row['reason_code']!='SOURCE_SUPPORTED' or not result.get('quote'):
                    raise ValueError('Accepted analysis requires source-supported quote')
            digest = hashlib.sha256(saved[1].encode('utf-8')).hexdigest()
            if row['result_sha256'] != digest:
                raise ValueError('Review response hash mismatch')
            db.execute('''INSERT INTO analysis_reviews(owner_id,item_key,version,
              response_sha256,decision,reason_code,review_version,reviewed_at)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,item_key,version)
              DO UPDATE SET response_sha256=excluded.response_sha256,
              decision=excluded.decision,reason_code=excluded.reason_code,
              review_version=excluded.review_version,reviewed_at=excluded.reviewed_at''',
              (owner_id(),row['item_key'],row['version'],digest,row['decision'],
               row['reason_code'],payload['review_version'],datetime.now(timezone.utc).isoformat()))
            counts[row['decision']] += 1
    return {'reviewed':len(rows),'counts':counts,'review_version':payload['review_version']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('private_review_json',type=Path)
    args = parser.parse_args()
    print(json.dumps(apply_reviews(args.private_review_json),sort_keys=True))
