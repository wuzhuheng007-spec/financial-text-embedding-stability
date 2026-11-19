"""Current-vintage proxy construction and sample-prefix sensitivity, not a return test."""
from pathlib import Path
import argparse,hashlib,json,sys,datetime
import numpy as np
import pandas as pd

EXPECTED='3580398aaba6b72ad988844416c7b0ac9caca24fd2ab1d9149a983ec63b9f7a2'
NAMES=['cefd','nipo_12m','ripo_weighted_12m_lag12','pdnd_lag12','s']
CUTOFFS=[201012,201612,202012,202512]

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def features(data):
    a=data.copy();a.index=pd.PeriodIndex(a.yearmo.astype(str),freq='M')
    assert a.index.is_unique and a.index.is_monotonic_increasing
    assert a.index.equals(pd.period_range(a.index[0],a.index[-1],freq='M'))
    ipo=a.nipo.astype(float);ripo=a.ripo.astype(float).copy()
    ripo.loc[(ipo==0)|ripo.isna()]=0
    total=ipo.rolling(12,min_periods=12).sum()
    weighted=(ipo*ripo).rolling(12,min_periods=12).sum()/total.replace(0,np.nan)
    x=pd.DataFrame({'cefd':a.cefd,'nipo_12m':total,'ripo_weighted_12m_lag12':weighted.shift(12),'pdnd_lag12':a.pdnd.shift(12),'s':a.s})
    x['yearmo']=a.yearmo;x['published_SENT']=a.SENT
    return x[x.yearmo>=196507]

def fit(x):
    a=x[NAMES].dropna();mean=a.mean();std=a.std(ddof=1)
    assert (std>0).all()
    z=(a-mean)/std;cor=z.to_numpy().T@z.to_numpy()/(len(z)-1)
    values,vectors=np.linalg.eigh(cor);loading=vectors[:,-1]
    if loading[NAMES.index('nipo_12m')]<0:loading=-loading
    raw=z.to_numpy()@loading;score=(raw-raw.mean())/raw.std(ddof=1)
    return pd.Series(score,index=a.index),{'n_complete_months':len(a),'first_month':str(a.index[0]),'last_month':str(a.index[-1]),'mean':mean.to_dict(),'std_ddof1':std.to_dict(),'pc1_loading':dict(zip(NAMES,loading.tolist())),'pc1_eigenvalue':float(values[-1]),'pc1_variance_fraction':float(values[-1]/values.sum())}

def run(workbook,author_workbook,out):
    assert sha(workbook)==EXPECTED
    root=Path(__file__).resolve().parent;freeze=json.loads((root/'freeze_receipt.json').read_text())
    assert sha(root/'PLAN_zh.md')==freeze['plan_sha256']
    data=pd.read_excel(workbook,sheet_name='DATA');other=pd.read_excel(author_workbook,sheet_name='DATA')
    assert data.equals(other),'NYU and pinned GitHub DATA differ'
    out.mkdir(parents=True,exist_ok=False)
    x=features(data);full,full_fit=fit(x[x.yearmo<=202512]);published=x.loc[full.index,'published_SENT'].astype(float)
    pairs=pd.DataFrame({'diagnostic_proxy_pc1':full,'published_SENT':published}).dropna()
    corr=float(pairs.corr().iloc[0,1]);diff=pairs.diagnostic_proxy_pc1-pairs.published_SENT
    comparison={'n_paired_months':len(pairs),'correlation':corr,'mean_absolute_difference':float(diff.abs().mean()),'max_absolute_difference':float(diff.abs().max()),'rmse':float(np.sqrt((diff**2).mean())),'label':'CURRENT_VINTAGE_NUMERICAL_COMPARISON_NOT_CERTIFIED_STATA_OR_PIT_REPLICATION'}
    summaries=[];fit_details={};scores=[]
    for cutoff in CUTOFFS:
        score,detail=fit(x[x.yearmo<=cutoff]);fit_details[str(cutoff)]=detail
        common=score.index.intersection(full.index);a=score.loc[common];b=full.loc[common];delta=a-b
        summaries.append({'fit_end_yearmo':cutoff,'shared_historical_months':len(common),'correlation_with_full_window':float(a.corr(b)),'mean_absolute_score_revision':float(delta.abs().mean()),'max_absolute_score_revision':float(delta.abs().max()),'rms_score_revision':float(np.sqrt((delta**2).mean())),'last_shared_month':str(common[-1]),'prefix_score_at_last_shared_month':float(a.iloc[-1]),'full_window_score_at_same_month':float(b.iloc[-1])})
        scores.extend({'month':str(month),'fit_end_yearmo':cutoff,'prefix_score':float(score.loc[month]),'full_window_score':float(full.loc[month])} for month in common)
    pd.DataFrame(summaries).to_csv(out/'prefix_sensitivity.csv',index=False)
    # These are third-party derived rows: private diagnostic evidence, not public payload.
    pd.DataFrame(scores).to_csv(out/'private_prefix_scores.csv',index=False)
    pairs.to_csv(out/'private_published_comparison.csv')
    write(out/'published_comparison.json',comparison);write(out/'fit_details.json',fit_details)
    summary={'status':'EXECUTED_CURRENT_VINTAGE_CONSTRUCTION_DIAGNOSTIC','author_code_commit':freeze['author_commit'],'workbook_DATA_equal_across_NYU_and_GitHub':True,'NYU_workbook_sha256':sha(workbook),'GitHub_workbook_sha256':sha(author_workbook),'NYU_workbook_bytes_equal_to_GitHub':workbook.read_bytes()==author_workbook.read_bytes(),'published_comparison':comparison,'prefix_summary':summaries,'raw_proxy_fit':full_fit,'not_done':['No Stata run','No historical publication-vintage reconstruction','No SENT_ORTH reconstruction','No market-return regression','No sentiment-change differencing','No strategy or alpha test'],'input_unchanged':sha(workbook)==EXPECTED}
    write(out/'summary.json',summary)
    write(out/'run_manifest.json',{'run_id':'bw-construction-diagnostic-20261119-v1','timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'command':sys.argv,'code_sha256':sha(__file__),'plan_sha256':freeze['plan_sha256'],'NYU_workbook_sha256':sha(workbook),'author_workbook_sha256':sha(author_workbook),'numpy':np.__version__,'pandas':pd.__version__,'seed':None,'seed_reason':'Deterministic eigendecomposition; no stochastic estimator or bootstrap','outputs':{p.name:sha(p) for p in out.iterdir()},'claim_scope':'Construction/window sensitivity only, not financial prediction'})
    print(json.dumps({'comparison':comparison,'prefix_summary':summaries,'DATA_tables_equal':True}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workbook',type=Path,required=True);p.add_argument('--author-workbook',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();run(a.workbook,a.author_workbook,a.out)
