"""Inventory every earlier tracked workbook snapshot; preserve incomplete uploads."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
SRC=ROOT/'source_audit'

def main():
    registry=json.loads((SRC/'revision_history.json').read_text())
    latest=pd.read_excel(SRC/'SENTIMENT_github.xlsx',sheet_name='DATA').set_index('yearmo')
    inventory=[];comparisons=[]
    snapshots=registry['snapshots']+[{'commit':'7a0165f324f06a0f88aafc253de18c63431d2a9d','date':'2026-04-21T21:51:27Z','message':'Correcting upload of output','file':'SENTIMENT_github.xlsx','sha256':hashlib.sha256((SRC/'SENTIMENT_github.xlsx').read_bytes()).hexdigest(),'blob_sha':'a56f31f1e70b45b0002b1f653a6793e6315d306c'}]
    for spec in sorted(snapshots,key=lambda x:x['date']):
        f=SRC/spec['file'];data=f.read_bytes()
        assert hashlib.sha256(data).hexdigest()==spec['sha256']
        excel=pd.ExcelFile(f);row={**{k:spec[k] for k in ['commit','date','file','sha256','blob_sha']},'sheets':'|'.join(excel.sheet_names),'status':'NO_DATA_SHEET','data_rows':0,'first_yearmo':None,'last_yearmo':None}
        if 'DATA' not in excel.sheet_names:inventory.append(row);continue
        old=pd.read_excel(f,sheet_name='DATA');assert not old.yearmo.duplicated().any()
        row.update({'status':'DATA_PRESENT_CHECK_SCOPE','data_rows':len(old),'first_yearmo':int(old.yearmo.min()),'last_yearmo':int(old.yearmo.max()),'SENT_nonmissing':int(old.SENT.notna().sum()),'SENT_ORTH_nonmissing':int(old.SENT_ORTH.notna().sum())})
        if old.yearmo.max()<202501 and spec['date'].startswith('2026'):row['status']='DATA_PRESENT_TRUNCATED_RELATIVE_TO_CURRENT_YEAR_SPAN'
        inventory.append(row);old=old.set_index('yearmo')
        for measure in ['SENT','SENT_ORTH']:
            pair=pd.concat([old[measure].rename('old'),latest[measure].rename('latest')],axis=1).dropna()
            delta=pair.latest-pair.old
            comparisons.append({'old_commit':spec['commit'],'old_commit_date':spec['date'],'measure':measure,'paired_months':len(pair),'first_common_yearmo':int(pair.index.min()),'last_common_yearmo':int(pair.index.max()),'mean_absolute_revision':float(delta.abs().mean()),'max_absolute_revision':float(delta.abs().max()),'correlation':float(pair.old.corr(pair.latest)),'months_changed_over_1e_8':int((delta.abs()>1e-8).sum()),'attribution':'Combined file-version difference; not an isolated effect of data revisions, sample extension or methodology'})
    out=ROOT/'results/revision_audit';out.mkdir(parents=True,exist_ok=False)
    pd.DataFrame(inventory).to_csv(out/'workbook_inventory.csv',index=False)
    pd.DataFrame(comparisons).to_csv(out/'published_version_comparison.csv',index=False)
    result={'status':'VERIFIED_WORKBOOK_VERSION_INVENTORY_NOT_PIT_ARCHIVE','tracked_workbook_versions_inspected':len(inventory),'versions_with_DATA_sheet':sum(x['data_rows']>0 for x in inventory),'no_DATA_sheet_versions':sum(x['data_rows']==0 for x in inventory),'inventory':inventory,'comparisons':comparisons,'github_commit_dates_are_first_publication_dates':False,'historical_monthly_release_archive_reconstructed':False,'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({'versions':len(inventory),'inventory':inventory,'comparisons':comparisons}))

if __name__=='__main__':main()
