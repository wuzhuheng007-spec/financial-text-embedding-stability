"""Check published manifests, append-only evidence hashes and local claim references."""
from pathlib import Path
import hashlib,json

def verify(root):
    manifest=json.loads((root/'PUBLICATION_MANIFEST.json').read_text())
    for name,want in manifest.items():
        f=root/name
        assert f.is_file(),name
        assert hashlib.sha256(f.read_bytes()).hexdigest()==want,name
    steps=json.loads((root/'steps.json').read_text());seen=set()
    for step in steps:
        assert step['id'] not in seen;seen.add(step['id'])
        for name,want in step['evidence_sha256'].items():
            assert hashlib.sha256((root/name).read_bytes()).hexdigest()==want,(step['id'],name)
    claims=json.loads((root/'claims.json').read_text())
    for claim in claims:
        for name in claim.get('evidence',[]):assert (root/name).is_file(),(claim['id'],name)
    status=json.loads((root/'STATUS.json').read_text())
    assert status['latest_step_id'] in seen
    return {'status':'PASS','published_files_hashed':len(manifest),'steps_checked':len(steps),'claims_checked':len(claims),'independent_research_validation':False}

if __name__=='__main__':print(json.dumps(verify(Path(__file__).resolve().parent)))
