"""Compile ONLY the frozen project wire-v2 conditions into explicit alternatives.

This is a decoding-schema candidate, NOT a replacement acceptance validator.
Original wire-v2 and canonical checks remain authoritative. Backend support
and tokenizer compatibility must be tested separately before any model call.
"""
from copy import deepcopy
from itertools import product
import hashlib
import json
from pathlib import Path
import argparse

PINNED_SHA256 = 'afc517b5c6ef96e06d89380905e51843cecc6b34aa31888c5ca14e0ee07b2022'

def enumize(x):
    if isinstance(x,list): return [enumize(y) for y in x]
    if not isinstance(x,dict): return x
    out={k:enumize(v) for k,v in x.items() if k!='const'}
    if 'const' in x: out['enum']=[x['const']]
    return out

def compile_bytes(raw):
    if hashlib.sha256(raw).hexdigest()!=PINNED_SHA256:
        raise ValueError('Only the exact pinned wire-v2 schema is supported')
    source=json.loads(raw)
    defs=deepcopy(source['$defs'])
    reasons=source['$defs']['unknown_reason']['enum']
    record=deepcopy(defs['company_event']); record.pop('allOf')
    alternatives=[]
    for indirect,inference,unknown in product((False,True),repeat=3):
        b=deepcopy(record); p=b['properties']
        for field,flag,special in [('impact_path',indirect,'INDIRECT'),
                ('statement_status',inference,'ANNOTATOR_INFERENCE'),
                ('business_impact_direction',unknown,'UNKNOWN')]:
            old=p[field]['enum'];p[field]['enum']=[special] if flag else [v for v in old if v!=special]
        if indirect:p['relationship_evidence_spans']['minItems']=1
        if inference:p['inference_premise']={'type':'string','minLength':1}
        if unknown:p['unknown_reason']={'enum':[v for v in reasons if v!='NONE']}
        alternatives.append(b)
    defs['company_event']={'anyOf':alternatives}
    root=deepcopy(source)
    for k in ('$defs','allOf','$schema','$id','title','description'):root.pop(k,None)
    branches=[]
    for disposition,market in product(source['properties']['article_disposition']['enum'],['YES','NO','UNKNOWN']):
        b=deepcopy(root);p=b['properties']
        p['article_disposition']={'enum':[disposition]}
        p['market_outcome_reported']={'enum':[market]}
        if disposition=='COMPANY_EVENTS_FOUND':
            p['records']['minItems']=1;p['article_unknown_reason']={'enum':['NONE']}
        else:
            p['records']['maxItems']=0
            reason={'NO_IDENTIFIABLE_EVENT':['NO_EVENT'],'NO_SUPPORTED_COMPANY':['NO_COMPANY_LINK'],
                    'UNDETERMINED':[v for v in reasons if v!='NONE']}[disposition]
            p['article_unknown_reason']={'enum':reason}
        if market=='NO':p['market_outcome_direction']={'enum':['NOT_REPORTED']}
        if market=='YES':p['market_outcome_direction']['enum']=[v for v in p['market_outcome_direction']['enum'] if v!='NOT_REPORTED']
        branches.append(b)
    return enumize({'$schema':source['$schema'],'$defs':defs,'anyOf':branches})

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('output',type=Path)
    a=ap.parse_args()
    if a.output.exists():raise ValueError('Use a new output path')
    result=compile_bytes(a.source.read_bytes())
    a.output.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
