"""Separate same-author construction checks with direct rolling loops and SVD."""
from pathlib import Path
import argparse,hashlib,json,statistics
import numpy as np
import pandas as pd
import diagnose_bw

def check(workbook,results):
    data=pd.read_excel(workbook,sheet_name='DATA');features=diagnose_bw.features(data)
    ipos=data.nipo.to_numpy(float);ripo=data.ripo.to_numpy(float)
    ipo_windows=0
    for i in range(23,len(data)):
        ym=int(data.yearmo.iloc[i]);month=pd.Period(str(ym),freq='M')
        if ym<196507:continue
        counts=ipos[i-11:i+1]
        if np.isfinite(counts).all():
            assert np.isclose(features.loc[month,'nipo_12m'],sum(counts),rtol=1e-12)
        j=i-12;c=ipos[j-11:j+1];v=ripo[j-11:j+1].copy();v[(c==0)|~np.isfinite(v)]=0
        if np.isfinite(c).all() and c.sum()>0:
            manual=sum(float(x*y) for x,y in zip(c,v))/sum(c)
            assert np.isclose(features.loc[month,'ripo_weighted_12m_lag12'],manual,rtol=1e-12)
            ipo_windows+=1
        assert np.isclose(features.loc[month,'pdnd_lag12'],data.pdnd.iloc[i-12],equal_nan=True)
    stored=pd.read_csv(results/'private_prefix_scores.csv');max_error=0.0
    for cutoff in diagnose_bw.CUTOFFS:
        x=features[features.yearmo<=cutoff][diagnose_bw.NAMES].dropna()
        a=x.to_numpy();means=np.array([statistics.mean(a[:,k]) for k in range(a.shape[1])]);stds=np.array([statistics.stdev(a[:,k]) for k in range(a.shape[1])])
        z=(a-means)/stds;u,s,vh=np.linalg.svd(z,full_matrices=False);loading=vh[0]
        if loading[1]<0:loading=-loading
        score=z@loading;score=(score-score.mean())/score.std(ddof=1)
        recorded=stored[stored.fit_end_yearmo==cutoff]
        assert list(recorded.month)==[str(m) for m in x.index]
        error=float(np.max(np.abs(score-recorded.prefix_score.to_numpy())))
        assert error<1e-10;max_error=max(max_error,error)
    # Future data mutations cannot alter a properly cutoff-fitted prefix diagnostic.
    prefix=features[features.yearmo<=201012];original,_=diagnose_bw.fit(prefix)
    altered=data.copy();altered.loc[altered.yearmo>201012,['nipo','ripo','cefd','s','pdnd']]*=7
    rebuilt=diagnose_bw.features(altered);changed,_=diagnose_bw.fit(rebuilt[rebuilt.yearmo<=201012])
    assert np.allclose(original,changed,rtol=0,atol=1e-12)
    summary=json.loads((results/'summary.json').read_text());full=stored[stored.fit_end_yearmo==202512]
    for r in summary['prefix_summary']:
        a=stored[stored.fit_end_yearmo==r['fit_end_yearmo']];d=a.prefix_score-a.full_window_score
        assert np.isclose(abs(d).mean(),r['mean_absolute_score_revision'],rtol=1e-12,atol=1e-12)
        assert np.isclose(abs(d).max(),r['max_absolute_score_revision'],rtol=1e-12,atol=1e-12)
    out={'status':'PASS','validation_type':'Same-author direct window aggregation and SVD cross-check; not independent audit','independent_validation':False,'weighted_IPO_windows_checked':ipo_windows,'prefix_fits_recomputed':len(diagnose_bw.CUTOFFS),'max_eigen_vs_svd_score_difference':max_error,'future_input_perturbation_prefix_unchanged':True,'workbook_sha256':hashlib.sha256(workbook.read_bytes()).hexdigest(),'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'financial_efficacy_tested':False}
    (results/'self_check.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workbook',type=Path,required=True);p.add_argument('--results',type=Path,required=True);a=p.parse_args();check(a.workbook,a.results)
