"""Recompute source features, refit models and reconcile all aggregate cells."""
from pathlib import Path
import datetime,hashlib,json
import numpy as np
import pandas as pd
R=Path(__file__).resolve().parent;O=R/'results';P=R/'private';C=json.loads((R/'config.json').read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    m=json.loads((O/'run_manifest.json').read_text());checks={}
    assert sha(R/'analyze.py')==m['code_sha256'] and sha(R/'config.json')==m['config_sha256']
    for n,h in m['outputs'].items():assert sha(R/n)==h,n
    for x in json.loads((R/'input_manifest.json').read_text()):assert sha(R/'raw'/f"{x['series']}.csv")==x['sha256']
    raw={sid:pd.read_csv(R/'raw'/f'{sid}.csv',index_col=0,parse_dates=True).apply(pd.to_numeric,errors='coerce').dropna().iloc[:,0] for sid in ['NASDAQNDXE','NASDAQ100','DFII10']}
    dates=raw['NASDAQNDXE'].index.intersection(raw['NASDAQ100'].index);eq=raw['NASDAQNDXE'].loc[dates];cap=raw['NASDAQ100'].loc[dates]
    unmatched=raw['NASDAQNDXE'].loc[dates[0]:dates[-1]].index.symmetric_difference(raw['NASDAQ100'].loc[dates[0]:dates[-1]].index)
    expected=pd.DataFrame(index=dates);seg_start=0
    for j,d in enumerate(dates):
        if j and ((unmatched>dates[j-1])&(unmatched<d)).any():seg_start=j
        for n in [20,60]:
            expected.loc[d,f'breadth{n}']=float((eq.iloc[j]/eq.iloc[j-n]-1)-(cap.iloc[j]/cap.iloc[j-n]-1)) if j-seg_start>=n else np.nan
        expected.loc[d,'cap_return60']=float(cap.iloc[j]/cap.iloc[j-60]-1) if j-seg_start>=60 else np.nan
    ps={};alignments=0;feature_cells=0;mins=0;parents=0
    for sym in C['symbols']:
        name=sym.replace('.','_')+'_panel.csv';a=pd.read_csv(P/name,index_col=0,parse_dates=True);ps[sym]=a
        parent=R.parent/'sentiment_phase3_20261119/private_v1_1'/name
        assert sha(parent)==C['parent_panel_hashes'][str(parent.relative_to(R.parent))]
        b=pd.read_csv(parent,index_col=0,parse_dates=True)
        assert a.index.equals(b.index)
        for f in ['close','return20','return60','ma60_gap','drawdown252','volatility20','future4','future8','future12']:
            np.testing.assert_allclose(a[f],b[f],atol=1e-12,equal_nan=True)
        parents+=len(a)
        for h in [4,8,12]:
            a[f'end{h}']=pd.to_datetime(a[f'end{h}'])
            for j,end in enumerate(a[f'end{h}']):
                if pd.isna(end):assert pd.isna(a[f'min_return{h}'].iloc[j]);continue
                k=a.index.get_loc(end);expected_min=min(a.close.iloc[j+1:k+1]/a.close.iloc[j+1]-1)
                np.testing.assert_allclose(expected_min,a[f'min_return{h}'].iloc[j],atol=1e-12);mins+=1
        for j,d in enumerate(a.index):
            ix=int(dates.searchsorted(d,side='left')-2)
            rd=raw['DFII10'].index;ri=int(rd.searchsorted(d,side='left')-2)
            for prefix,ds,i in [('participation',dates,ix),('real',rd,ri)]:
                ed=ds[i] if i>=0 and (d-ds[i]).days<=7 else pd.NaT
                got=pd.Timestamp(a[prefix+'_source_date'].iloc[j])
                assert (pd.isna(ed) and pd.isna(got)) or ed==got
                alignments+=1
            target=[np.nan]*5
            if ix>=0 and (d-dates[ix]).days<=7:target[:3]=expected.iloc[ix][['breadth20','breadth60','cap_return60']].tolist()
            if ri>=0 and (d-rd[ri]).days<=7:
                target[3]=raw['DFII10'].iloc[ri]
                target[4]=raw['DFII10'].iloc[ri]-raw['DFII10'].iloc[ri-20] if ri>=20 else np.nan
            np.testing.assert_allclose(a.iloc[j][['breadth20','breadth60','cap_return60','real_level','real_change20']].to_numpy(float),target,atol=1e-12,equal_nan=True);feature_cells+=5
        np.testing.assert_allclose(a.breadth_real,a.breadth60*a.real_change20,atol=1e-12,equal_nan=True)
        np.testing.assert_allclose(a.risk_real,a.risk_appetite/100*a.real_change20,atol=1e-12,equal_nan=True)
        known=a[['breadth60','cap_return60']].notna().all(axis=1)
        assert a.loc[~known,'state'].isna().all()
        assert a.loc[known&a.cap_return60.gt(0)&a.breadth60.lt(0),'state'].eq('narrowing').all()
        assert a.loc[known&a.cap_return60.gt(0)&a.breadth60.ge(0),'state'].eq('broadening').all()
    checks.update(parent_price_feature_outcome_rows_unchanged=parents,source_alignment_dates=alignments,new_source_feature_cells=feature_cells,forward_close_path_minima=mins)
    fits=json.loads((P/'fits.json').read_text());pred=pd.read_csv(P/'predictions.csv',parse_dates=['date'])
    full=C['models']['base']+C['models']['add_joint'];pred_cells=0
    for f in fits:
        a=ps[f['symbol']].dropna(subset=full+[f"future{f['horizon_weeks']}"]);h=f['horizon_weeks'];cut=pd.Timestamp(f['year'],1,1)
        train=a.loc[(a.index<cut)&a[f'end{h}'].lt(cut)];test=a.loc[a.index.year==f['year']]
        assert len(train)==f['train_n']>=756 and train[f'end{h}'].max()<cut
        x=train[f['features']].to_numpy();y=train[f'future{h}'].to_numpy();mu=x.mean(0);sd=x.std(0);sd=np.where(sd>1e-12,sd,1);z=(x-mu)/sd
        beta=np.linalg.lstsq(np.vstack([z,np.sqrt(f['alpha'])*np.eye(z.shape[1])]),np.r_[y-y.mean(),np.zeros(z.shape[1])],rcond=None)[0]
        np.testing.assert_allclose(beta,f['params']['beta'],atol=2e-9)
        q=pred.loc[pred.symbol.eq(f['symbol'])&pred.horizon_weeks.eq(h)&pred.year.eq(f['year'])&pred.alpha.eq(f['alpha'])]
        assert pd.DatetimeIndex(q.date).equals(test.index)
        yp=y.mean()+(test[f['features']].to_numpy()-mu)/sd@beta
        np.testing.assert_allclose(yp,q[f['model']],atol=2e-9);pred_cells+=len(q)
    checks.update(augmented_system_refitted_models=len(fits),prediction_cells_reconciled=pred_cells)
    summary=pd.read_csv(O/'model_summary.csv');con=pd.read_csv(O/'model_contrasts.csv')
    for _,x in summary.iterrows():
        b=pred.loc[pred.symbol.eq(x.symbol)&pred.horizon_weeks.eq(x.horizon_weeks)&pred.alpha.eq(x.alpha)]
        if x.period!='all':b=b.loc[b.period.eq(x.period)]
        assert len(b)==x.n;err=b[x.model]-b.actual
        np.testing.assert_allclose([np.mean(err**2),np.mean(abs(err)),np.mean(np.sign(b[x.model])==np.sign(b.actual))],[x.MSE,x.MAE,x.sign_accuracy],atol=1e-12)
    for _,x in con.iterrows():
        b=pred.loc[pred.symbol.eq(x.symbol)&pred.horizon_weeks.eq(x.horizon_weeks)&pred.alpha.eq(x.alpha)]
        if x.period!='all':b=b.loc[b.period.eq(x.period)]
        de=(b[x.model]-b.actual)**2-(b.base-b.actual)**2
        np.testing.assert_allclose([de.mean(),de.mean()/np.mean((b.base-b.actual)**2)],[x.delta_MSE,x.relative_MSE_change],atol=1e-12)
    checks.update(model_summary_rows=len(summary),paired_model_contrasts=len(con))
    ss=pd.read_csv(O/'state_summary.csv');sc=pd.read_csv(O/'state_contrasts.csv')
    for _,x in ss.iterrows():
        a=ps[x.symbol].dropna(subset=full+[f'future{x.horizon_weeks}']);a=a.loc[a.state.eq(x.state)]
        if x.period!='all':a=a.loc[a.period.eq(x.period)]
        assert len(a)==x.n
        if len(a):np.testing.assert_allclose([a[f'future{x.horizon_weeks}'].mean(),a[f'future{x.horizon_weeks}'].lt(0).mean(),a[f'min_return{x.horizon_weeks}'].le(-.1).mean()],[x.mean_forward_return,x.negative_final_fraction,x.minus10_close_path_fraction],atol=1e-12)
    for _,x in sc.iterrows():
        a=ss.loc[ss.symbol.eq(x.symbol)&ss.horizon_weeks.eq(x.horizon_weeks)&ss.period.eq(x.period)]
        n=a.loc[a.state.eq('narrowing')].iloc[0];b=a.loc[a.state.eq('broadening')].iloc[0]
        np.testing.assert_allclose(n.mean_forward_return-b.mean_forward_return,x.mean_return_narrow_minus_broad,atol=1e-12,equal_nan=True)
    checks.update(state_summary_rows=len(ss),state_contrast_rows=len(sc))
    # A source index prefix must give identical earlier retrospective-free features.
    for cut in [1000,3000,len(eq)-1]:
        e=eq.iloc[:cut+1];v=cap.iloc[:cut+1]
        for n in [20,60]:np.testing.assert_allclose((e/e.shift(n)-v/v.shift(n)),(eq/eq.shift(n)-cap/cap.shift(n)).iloc[:cut+1],atol=1e-12,equal_nan=True)
    checks['source_prefix_comparisons']=6
    result={'status':'PASS_SAME_AUTHOR_SELF_CHECK','independent_validation':False,'checks':checks,'run_manifest_sha256':sha(O/'run_manifest.json'),'check_code_sha256':sha(__file__),'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'limits':['current vintage','correlated markets','one deterministic model family','China inputs external only','not a news-reaction or strategy backtest']}
    (O/'self_check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
if __name__=='__main__':main()
