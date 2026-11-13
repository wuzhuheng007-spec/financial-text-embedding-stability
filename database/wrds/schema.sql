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
