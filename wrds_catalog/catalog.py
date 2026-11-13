"""Build a public catalog from allowlisted metadata, never from a row database."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import tempfile
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE artifacts(
 artifact_id TEXT PRIMARY KEY,name TEXT NOT NULL,size_bytes INTEGER,
 sha256 TEXT,materialization_status TEXT NOT NULL,category TEXT NOT NULL,
 classification_basis TEXT NOT NULL,publication TEXT NOT NULL,
 source_channel TEXT NOT NULL,archive_members INTEGER,crc_status TEXT NOT NULL,
 CHECK(size_bytes IS NULL OR size_bytes>=0),CHECK(sha256 IS NULL OR length(sha256)=64));
CREATE INDEX artifact_content ON artifacts(sha256);
CREATE TABLE datasets(dataset_id TEXT PRIMARY KEY,title TEXT NOT NULL,
 unit TEXT NOT NULL,description TEXT NOT NULL,admission_status TEXT NOT NULL);
CREATE TABLE source_evidence(evidence_id TEXT PRIMARY KEY,artifact_id TEXT,
 member_name TEXT,sha256 TEXT,verification_status TEXT NOT NULL,
 source_note TEXT NOT NULL,FOREIGN KEY(artifact_id) REFERENCES artifacts);
CREATE TABLE field_roles(dataset_id TEXT NOT NULL,field_name TEXT NOT NULL,
 role TEXT NOT NULL,date_semantics TEXT NOT NULL,status TEXT NOT NULL,
 evidence_id TEXT,PRIMARY KEY(dataset_id,field_name),
 FOREIGN KEY(dataset_id) REFERENCES datasets,FOREIGN KEY(evidence_id) REFERENCES source_evidence);
CREATE TABLE aggregate_results(result_id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,
 metric TEXT NOT NULL,value REAL,unit TEXT NOT NULL,as_of TEXT NOT NULL,
 evidence_id TEXT NOT NULL,scope TEXT NOT NULL,source_json_pointer TEXT,
 verification_status TEXT NOT NULL,
 FOREIGN KEY(dataset_id) REFERENCES datasets,FOREIGN KEY(evidence_id) REFERENCES source_evidence);
CREATE TABLE audit_runs(run_id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,
 operation TEXT NOT NULL,exit_code INTEGER,code_sha256 TEXT,config_sha256 TEXT,
 input_sha256 TEXT,summary_sha256 TEXT,evidence_id TEXT,code_commit TEXT,
 FOREIGN KEY(dataset_id) REFERENCES datasets,FOREIGN KEY(evidence_id) REFERENCES source_evidence);
CREATE TABLE run_inputs(run_id TEXT NOT NULL,input_role TEXT NOT NULL,sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,input_role),FOREIGN KEY(run_id) REFERENCES audit_runs);
CREATE TABLE open_issues(issue_id TEXT PRIMARY KEY,dataset_id TEXT,
 description TEXT NOT NULL,status TEXT NOT NULL,required_source TEXT NOT NULL,
 FOREIGN KEY(dataset_id) REFERENCES datasets);
CREATE TABLE official_sources(source_id TEXT PRIMARY KEY,title TEXT NOT NULL,url TEXT NOT NULL,
 accessed_date TEXT NOT NULL,scope TEXT NOT NULL);
CREATE VIEW duplicate_contents AS
 SELECT sha256,COUNT(*) occurrences,GROUP_CONCAT(name,'; ') names FROM artifacts
 WHERE sha256 IS NOT NULL AND materialization_status='PRESENT_HASH_VERIFIED'
 GROUP BY sha256 HAVING COUNT(*)>1;
CREATE VIEW catalog_counts AS
 SELECT category,materialization_status,COUNT(*) artifacts FROM artifacts
 GROUP BY category,materialization_status;
CREATE VIEW latest_results AS
 SELECT r.* FROM aggregate_results r WHERE NOT EXISTS(
 SELECT 1 FROM aggregate_results newer WHERE newer.dataset_id=r.dataset_id
 AND newer.metric=r.metric AND newer.as_of>r.as_of);
"""

COLUMNS = {
 'artifacts': ('artifact_id','name','size_bytes','sha256','materialization_status','category','classification_basis','publication','source_channel','archive_members','crc_status'),
 'datasets': ('dataset_id','title','unit','description','admission_status'),
 'source_evidence': ('evidence_id','artifact_id','member_name','sha256','verification_status','source_note'),
 'field_roles': ('dataset_id','field_name','role','date_semantics','status','evidence_id'),
 'aggregate_results': ('result_id','dataset_id','metric','value','unit','as_of','evidence_id','scope','source_json_pointer','verification_status'),
 'audit_runs': ('run_id','dataset_id','operation','exit_code','code_sha256','config_sha256','input_sha256','summary_sha256','evidence_id','code_commit'),
 'run_inputs': ('run_id','input_role','sha256'),
 'open_issues': ('issue_id','dataset_id','description','status','required_source'),
 'official_sources': ('source_id','title','url','accessed_date','scope'),
}
PRIVATE = re.compile(r"libfile_[a-z0-9]+|file_000000|(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/Users/|/home/|/workspace/|sediment://|[?&](?:token|sig|signature)=|-----BEGIN .*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]+",re.I)
SHA = re.compile(r'^[a-f0-9]{64}$')

def checked_payload(value):
    """Reject unknown columns and private transport references before publication."""
    if set(value) - (set(COLUMNS)|{'metadata'}):
        raise ValueError('Unknown public catalog section')
    metadata_keys={'as_of','inventory_scope','project_items_examined','scope_note','raw_rows_imported_here',
                   'formal_M5_status','schema_version','catalog_scope','inventory_sha256','reviewed_seed_sha256'}
    if set(value.get('metadata',{}))-metadata_keys:
        raise ValueError('Unknown public metadata key')
    if any(isinstance(v,(dict,list))for v in value.get('metadata',{}).values()):
        raise ValueError('Public metadata values must be scalar')
    for name,columns in COLUMNS.items():
        for row in value.get(name,[]):
            if set(row)-set(columns):
                raise ValueError(f'Unknown {name} fields')
            for key,item in row.items():
                if key.endswith('sha256') and item is not None and not SHA.fullmatch(item):
                    raise ValueError(f'Invalid {name}.{key}')
                if isinstance(item,(list,dict)):
                    raise ValueError('Row lists or nested row data are not public catalog fields')
            if name=='aggregate_results' and row.get('value') is not None and not isinstance(row['value'],(int,float)):
                raise ValueError('Aggregate values must be numeric or null')
            if name=='artifacts':
                n=row['name']
                if '/'in n or '\\'in n or n in ('.','..'):
                    raise ValueError('Artifact names must be basenames')
                if row.get('materialization_status')=='PRESENT_HASH_VERIFIED' and not row.get('sha256'):
                    raise ValueError('A verified artifact requires a content hash')
                if row.get('publication')!='METADATA_ONLY':
                    raise ValueError('Original file payloads cannot enter this catalog')
    if PRIVATE.search(json.dumps(value,ensure_ascii=False)):
        raise ValueError('Private location, account, credential or transport reference in public input')
    return value

def load_payload(inventory,reviewed):
    a=json.loads(Path(inventory).read_text(encoding='utf-8'))
    b=json.loads(Path(reviewed).read_text(encoding='utf-8'))
    if set(a)-{'metadata','artifacts'}:
        raise ValueError('Inventory accepts only metadata and artifact rows')
    out={**b,**a}
    out['metadata']={**b.get('metadata',{}),**a.get('metadata',{})}
    return checked_payload(out)

def build_catalog(inventory,reviewed,database):
    payload=load_payload(inventory,reviewed)
    dest=Path(database);dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.resolve()==Path(inventory).resolve()or dest.resolve()==Path(reviewed).resolve():
        raise ValueError('Output would overwrite an input')
    with tempfile.NamedTemporaryFile(dir=dest.parent,suffix='.sqlite',delete=False)as tmp:
        staging=Path(tmp.name)
    try:
        conn=sqlite3.connect(staging)
        try:
            conn.executescript(SCHEMA)
            meta={**payload.get('metadata',{}),'schema_version':'1','catalog_scope':'METADATA_AND_REVIEWED_AGGREGATES_ONLY',
                  'inventory_sha256':hashlib.sha256(Path(inventory).read_bytes()).hexdigest(),
                  'reviewed_seed_sha256':hashlib.sha256(Path(reviewed).read_bytes()).hexdigest()}
            conn.executemany('INSERT INTO metadata VALUES (?,?)',sorted((k,str(v))for k,v in meta.items()))
            for name,columns in COLUMNS.items():
                values=[tuple(row.get(c)for c in columns)for row in payload.get(name,[])]
                conn.executemany(f"INSERT INTO {name}({','.join(columns)}) VALUES({','.join('?'for _ in columns)})",values)
            conn.commit()
            if conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Catalog foreign key failure')
            if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Catalog integrity failure')
            conn.execute('VACUUM')
        finally:conn.close()
        staging.replace(dest)
    finally:
        staging.unlink(missing_ok=True)
    return validate_catalog(dest)

def validate_catalog(database):
    p=Path(database)
    conn=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        allowed=set(COLUMNS)|{'metadata','duplicate_contents','catalog_counts','latest_results'}
        observed={x[0]for x in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view','trigger')")}
        if observed!=allowed:
            raise ValueError('Unexpected or missing SQLite objects in public catalog')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Integrity check failed')
        if conn.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Foreign key check failed')
        rows={name:[dict(zip(columns,r))for r in conn.execute(f"SELECT {','.join(columns)} FROM {name}")]for name,columns in COLUMNS.items()}
        rows['metadata']=dict(conn.execute('SELECT key,value FROM metadata'))
        checked_payload(rows)
        counts={k:conn.execute(f'SELECT count(*) FROM {k}').fetchone()[0]for k in COLUMNS}
        return {'status':'PASS','counts':counts,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    finally:conn.close()

def export_catalog(database,destination):
    result=validate_catalog(database)
    root=Path(destination);root.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        payload={'metadata':dict(conn.execute('SELECT key,value FROM metadata'))}
        for name,columns in COLUMNS.items():
            cur=conn.execute(f'SELECT * FROM {name} ORDER BY 1')
            headings=[x[0]for x in cur.description];rows=cur.fetchall()
            payload[name]=[dict(zip(headings,row))for row in rows]
            with(root/f'{name}.csv').open('w',encoding='utf-8',newline='')as f:
                w=csv.writer(f);w.writerow(headings);w.writerows(rows)
        (root/'catalog.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    finally:conn.close()
    return result
