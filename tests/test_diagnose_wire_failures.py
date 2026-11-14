import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import diagnose_wire_failures as d

# Use the frozen wire Schema from this repository without mutating pytest argv.
SCHEMA = json.loads((ROOT / 'docs/news_research/company_event_wire_v2.schema.json').read_text(encoding='utf-8'))

def fixture():
    body = 'Acme launched a new product.'
    wire = {'schema_version':'n1_company_event_wire_v2',
        'article_disposition':'COMPANY_EVENTS_FOUND', 'article_unknown_reason':'NONE',
        'market_outcome_reported':'NO', 'market_outcome_direction':'NOT_REPORTED',
        'date_conflict':'UNKNOWN', 'identity_conflict':'UNKNOWN',
        'future_return_status':'NOT_OBSERVED', 'article_evidence':[],
        'records':[{'event_description':'Acme launched a product.', 'event_type':'product_launch',
            'target_company_text':'Acme', 'ticker_candidate':None, 'company_role':'SUBJECT',
            'identity_status':'TEXT_ONLY', 'business_impact_direction':'NO_CLEAR_IMPACT',
            'impact_path':'DIRECT', 'statement_status':'EXPLICIT_IN_ARTICLE',
            'inference_premise':None, 'unknown_reason':'NONE',
            'evidence_spans':[{'role':'EVENT','text':body},{'role':'TARGET_COMPANY','text':'Acme'}],
            'relationship_evidence_spans':[]}]}
    return body, wire

def row(body, wire, ordinal=1):
    return {'ordinal':ordinal,'body':body,'body_sha256':hashlib.sha256(body.encode()).hexdigest(),
            'response_text':json.dumps(wire)}

class DiagnosticTests(unittest.TestCase):
    def test_valid_quotes_and_immutable_input(self):
        body, wire = fixture(); rows=[row(body,wire)]; before=copy.deepcopy(rows)
        p,_=d.diagnose(rows,SCHEMA)
        self.assertEqual(p['quotes_from_wire_valid_only'],{'UNIQUE_EXACT':2})
        self.assertEqual(rows,before)

    def test_error_instances_are_not_response_counts(self):
        body,wire=fixture(); wire['records'][0]['company_role']='subject'
        wire['records'][0]['identity_status']='made_up'
        p,_=d.diagnose([row(body,wire)],SCHEMA)
        self.assertEqual(p['counts']['wire_invalid'],1)
        self.assertEqual(p['error_instances'],2)
        self.assertEqual(p['enum_patterns'],{'case_only':1,'other_unlisted_string':1})
        self.assertEqual(p['quotes_from_wire_valid_only'],{})

    def test_conditional_constraint_preserved(self):
        body,wire=fixture();wire['article_unknown_reason']='OTHER'
        p,_=d.diagnose([row(body,wire)],SCHEMA)
        self.assertEqual(p['error_groups'],[{'path':'/article_unknown_reason','keyword':'const','instances':1}])

    def test_json_failure_not_repaired(self):
        body,wire=fixture();r=row(body,wire);r['response_text']='```json\n{}\n```'
        p,_=d.diagnose([r],SCHEMA)
        self.assertEqual(p['counts']['json_invalid'],1)
        self.assertNotIn('wire_valid',p['counts'])

    def test_missing_and_overlapping_quotes(self):
        body,wire=fixture();body='aaa'
        wire['records'][0]['evidence_spans'][0]['text']='aa'
        wire['records'][0]['evidence_spans'][1]['text']='MISSING'
        p,_=d.diagnose([row(body,wire)],SCHEMA)
        self.assertEqual(p['quotes_from_wire_valid_only'],{'AMBIGUOUS':1,'NOT_FOUND':1})

    def test_host_integrity_rejects_bad_hash_and_duplicates(self):
        body,wire=fixture();r=row(body,wire);bad=copy.deepcopy(r);bad['body_sha256']='x'
        with self.assertRaises(ValueError):d.diagnose([bad],SCHEMA)
        with self.assertRaises(ValueError):d.diagnose([r,r],SCHEMA)

    def test_public_output_has_no_raw_values(self):
        body,wire=fixture();wire['records'][0]['company_role']='SECRET_BODY_VALUE'
        wire['SECRET_MODEL_KEY']='SECRET_OTHER_VALUE'
        p,private=d.diagnose([row(body,wire)],SCHEMA)
        self.assertNotIn('SECRET',json.dumps(p))
        self.assertIn('SECRET',json.dumps(private))

if __name__ == '__main__':
    unittest.main()
