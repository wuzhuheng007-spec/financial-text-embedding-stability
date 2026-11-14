"""No model/backend execution. Finite differential checks are not a full proof."""
from copy import deepcopy
from itertools import product
import argparse
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from compile_wire_v2 import compile_bytes

def fixture():
    return {'schema_version':'n1_company_event_wire_v2','article_disposition':'COMPANY_EVENTS_FOUND',
        'article_unknown_reason':'NONE','market_outcome_reported':'NO','market_outcome_direction':'NOT_REPORTED',
        'date_conflict':'UNKNOWN','identity_conflict':'UNKNOWN','future_return_status':'NOT_OBSERVED',
        'article_evidence':[], 'records':[{'event_description':'Acme launched a product.','event_type':'product_launch',
        'target_company_text':'Acme','ticker_candidate':None,'company_role':'SUBJECT','identity_status':'TEXT_ONLY',
        'business_impact_direction':'NO_CLEAR_IMPACT','impact_path':'DIRECT','statement_status':'EXPLICIT_IN_ARTICLE',
        'inference_premise':None,'unknown_reason':'NONE','evidence_spans':[{'role':'EVENT','text':'Acme launched a product.'},
        {'role':'TARGET_COMPANY','text':'Acme'}],'relationship_evidence_spans':[]}]}

def run(source):
    raw=source.read_bytes();original=json.loads(raw);compiled=compile_bytes(raw)
    Draft202012Validator.check_schema(compiled)
    validators=[Draft202012Validator(x) for x in (original,compiled)]
    counts={'cases':0,'accepted':0,'rejected':0}
    sentinels=[]
    def check(obj,name=None):
        answers=[v.is_valid(obj) for v in validators]
        assert answers[0]==answers[1], (name,obj,answers)
        counts['cases']+=1;counts['accepted' if answers[0] else 'rejected']+=1
        if name:sentinels.append({'name':name,'expected_valid':answers[0],'object':obj})
    base=fixture();check(base,'valid_company_event')
    for disp,reason,market,direction,present in product(original['properties']['article_disposition']['enum'],
            original['$defs']['unknown_reason']['enum'],['YES','NO','UNKNOWN'],
            original['properties']['market_outcome_direction']['enum'],[False,True]):
        x=deepcopy(base);x.update(article_disposition=disp,article_unknown_reason=reason,
            market_outcome_reported=market,market_outcome_direction=direction)
        if not present:x['records']=[]
        check(x)
    rp=original['$defs']['company_event']['properties']
    for direction,reason,path,status,premise,rel,evidence in product(rp['business_impact_direction']['enum'],
            original['$defs']['unknown_reason']['enum'],rp['impact_path']['enum'],rp['statement_status']['enum'],
            [None,'Supported synthetic premise'],[[],[{'role':'RELATIONSHIP','text':'Acme launched a product.'}]],
            [[],base['records'][0]['evidence_spans']]):
        x=deepcopy(base);r=x['records'][0]
        r.update(business_impact_direction=direction,unknown_reason=reason,impact_path=path,
                 statement_status=status,inference_premise=premise,relationship_evidence_spans=rel,evidence_spans=evidence)
        check(x)
    for name,path,value in [('no_market_wrong_direction',['market_outcome_direction'],'UNKNOWN'),
            ('wrong_reason_enum',['records',0,'unknown_reason'],'MADE_UP'),
            ('missing_one_evidence',['records',0,'evidence_spans'],[{'role':'EVENT','text':'Acme launched a product.'}]),
            ('no_event_with_record',['article_disposition'],'NO_IDENTIFIABLE_EVENT'),
            ('wrong_role',['records',0,'evidence_spans',0,'role'],'POSITIVE'),
            ('wrong_constant',['future_return_status'],'UP'),
            ('boolean_not_string',['date_conflict'],False),
            ('blank_evidence',['records',0,'evidence_spans',0,'text'],''),
            ('unknown_reason_none',['records',0,'business_impact_direction'],'UNKNOWN')]:
        x=deepcopy(base);parent=x
        for key in path[:-1]:parent=parent[key]
        parent[path[-1]]=value;check(x,name)
    for key in original['required']:
        x=deepcopy(base);del x[key];check(x,'missing_root_'+key)
    for key in original['$defs']['company_event']['required']:
        x=deepcopy(base);del x['records'][0][key];check(x,'missing_record_'+key)
    for extra in [False,True]:
        x=deepcopy(base);(x['records'][0] if extra else x)['unexpected']='x';check(x,'extra_record' if extra else 'extra_root')
    x=deepcopy(base);x.update(article_disposition='NO_IDENTIFIABLE_EVENT',article_unknown_reason='NO_EVENT',records=[])
    check(x,'valid_no_event')
    x=deepcopy(base);x.update(market_outcome_reported='YES',market_outcome_direction='UP')
    check(x,'valid_wire_needs_separate_market_evidence_check')
    try:compile_bytes(raw+b' ')
    except ValueError:counts['wrong_source_hash_rejected']=True
    else:raise AssertionError('hash guard')
    return compiled,counts,sentinels

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('out',type=Path);a=ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    compiled,counts,sentinels=run(a.source)
    payload=(json.dumps(compiled,separators=(',',':'))+'\n').encode()
    (a.out/'decoding_schema_candidate.json').write_bytes(payload)
    (a.out/'backend_sentinels.json').write_text(json.dumps(sentinels,indent=2)+'\n')
    result={'finite_differential_checks':counts,'schema_sha256':hashlib.sha256(payload).hexdigest(),
        'source_sha256':hashlib.sha256(a.source.read_bytes()).hexdigest(),'model_calls':0,
        'backend_tested':False,'tokenizer_tested':False,'is_full_equivalence_proof':False,
        'sentinels':len(sentinels)}
    (a.out/'compiler_validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
