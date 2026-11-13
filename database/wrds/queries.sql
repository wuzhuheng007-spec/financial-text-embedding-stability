-- File categories and explicit acquisition status.
SELECT category, materialization_status, artifacts
FROM catalog_counts ORDER BY category, materialization_status;

-- Actually acquired duplicate byte contents; retains all file aliases.
SELECT sha256, occurrences, names FROM duplicate_contents;

-- Every numeric result carries its unit, date and evidence link.
SELECT a.dataset_id,a.metric,a.value,a.unit,a.as_of,
       e.member_name,e.sha256,a.source_json_pointer,a.scope
FROM aggregate_results a JOIN source_evidence e USING(evidence_id)
ORDER BY a.dataset_id,a.metric,a.as_of;

-- Source versus research decisions still open.
SELECT issue_id,status,description,required_source FROM open_issues;

-- Declared source roles; never historical identity admission.
SELECT dataset_id,field_name,role,date_semantics,status FROM field_roles;
