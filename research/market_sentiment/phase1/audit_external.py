"""Audit the exact downloaded author workbook, without time alignment or factor testing."""
from pathlib import Path
import hashlib,json
import pandas as pd

p=Path(__file__).with_name('external_input_probe')
f=p/'BW_SENTIMENT.xlsx'
expected='3580398aaba6b72ad988844416c7b0ac9caca24fd2ab1d9149a983ec63b9f7a2'
assert hashlib.sha256(f.read_bytes()).hexdigest()==expected
d=pd.read_excel(f,sheet_name='DATA');notes=pd.read_excel(f,sheet_name='README',header=None)
assert not d.yearmo.duplicated().any() and d.yearmo.is_monotonic_increasing
result={'source_url':'https://pages.stern.nyu.edu/~jwurgler/data/SENTIMENT.xlsx','source_landing_url':'https://pages.stern.nyu.edu/~jwurgler/main.htm','source_sha256':expected,'workbook_sheets':['README','DATA'],'data_rows':len(d),'columns':list(d.columns),'time_unit':'monthly yearmo','duplicate_months':int(d.yearmo.duplicated().sum()),'measures':{},'notes':['Current-vintage workbook, README updated March2026. Values as of end of indicated period is not proof of public availability at that time.','README permits data revisions. Historical release calendar and expanding-window index construction not certified.','No BW/AAII factor efficacy run in phase1.','SENT_ORTH missing values are not filled.'],'audit_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
for k in ['SENT','SENT_ORTH']:
 a=d[d[k].notna()]
 result['measures'][k]={'nonmissing_months':len(a),'first_yearmo':int(a.yearmo.min()),'last_yearmo':int(a.yearmo.max()),'missing_within_nonmissing_span':d.loc[d.yearmo.between(a.yearmo.min(),a.yearmo.max())&d[k].isna(),'yearmo'].astype(int).tolist()}
(p/'BW_schema_audit.json').write_text(json.dumps(result,indent=2))
d.to_csv(p/'BW_DATA_current_vintage.csv',index=False)
(p/'BW_README_text.txt').write_text('\n'.join(' | '.join(str(v) for v in row if pd.notna(v)) for row in notes.itertuples(index=False,name=None)),encoding='utf-8')
print(json.dumps(result))
