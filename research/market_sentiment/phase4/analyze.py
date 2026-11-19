"""Phase4: current-vintage index participation and TIPS conditional diagnostics."""
from pathlib import Path
import datetime,hashlib,json,time,platform
import numpy as np
import pandas as pd
R=Path(__file__).resolve().parent;O=R/'results';P=R/'private'
C=json.loads((R/'config.json').read_text());BASE=C['models']['base'];NEW=C['models']['add_joint']
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(s):return pd.read_csv(R/'raw'/f'{s}.csv',index_col=0,parse_dates=True).apply(pd.to_numeric,errors='coerce').dropna().iloc[:,0]
def sources():
    eq=read('NASDAQNDXE');cap=read('NASDAQ100');real=read('DFII10')
    first=max(eq.index[0],cap.index[0]);last=min(eq.index[-1],cap.index[-1])
    missing=eq.loc[first:last].index.symmetric_difference(cap.loc[first:last].index)
    pair=pd.concat([eq.rename('eq'),cap.rename('cap')],axis=1).dropna().loc[first:last]
    segments=np.zeros(len(pair),int)
    for j in range(1,len(pair)):segments[j]=segments[j-1]+int(((missing>pair.index[j-1])&(missing<pair.index[j])).any())
    pair['pair_segment']=segments
    for _,b in pair.groupby('pair_segment'):
        for n in [20,60]:
            pair.loc[b.index,f'breadth{n}']=b['eq']/b['eq'].shift(n)-b.cap/b.cap.shift(n)
            pair.loc[b.index,f'cap_return{n}']=b.cap/b.cap.shift(n)-1
    rf=pd.DataFrame({'real_level':real,'real_change20':real-real.shift(20)})
    return pair,rf,{'pair_first':str(pair.index[0].date()),'pair_last':str(pair.index[-1].date()),'pair_rows':len(pair),'pair_segments':int(pair.pair_segment.nunique()),'asymmetric_source_dates':[str(d.date()) for d in missing],'NDXE_first':str(eq.index[0].date()),'real_first':str(real.index[0].date())}
def align(dates,source,fields,prefix):
    ix=source.index.searchsorted(dates,side='left')-2;safe=np.maximum(ix,0)
    valid=(ix>=0)&((dates-source.index[safe]).days<=7)
    out=pd.DataFrame(index=dates)
    ds=source.index[safe].to_numpy().copy();ds[~valid]=np.datetime64('NaT');out[prefix+'_source_date']=ds
    for f in fields:
        vals=source[f].iloc[safe].to_numpy(float).copy();vals[~valid]=np.nan;out[f]=vals
    return out
def panels():
    pair,rf,inventory=sources();ans={};cover=[]
    for sym in C['symbols']:
        f=R.parent/'sentiment_phase3_20261119/private_v1_1'/(sym.replace('.','_')+'_panel.csv')
        assert sha(f)==C['parent_panel_hashes'][str(f.relative_to(R.parent))]
        a=pd.read_csv(f,index_col='date',parse_dates=True)
        for h in [4,8,12]:a[f'end{h}']=pd.to_datetime(a[f'end{h}'])
        a=a.join(align(a.index,pair,['breadth20','breadth60','cap_return60'],'participation')).join(align(a.index,rf,['real_level','real_change20'],'real'))
        a['breadth_real']=a.breadth60*a.real_change20;a['risk_real']=a.risk_appetite/100*a.real_change20
        a['state']=None
        valid=a[['breadth60','cap_return60']].notna().all(axis=1)
        a.loc[valid,'state']='weak'
        a.loc[valid&a.cap_return60.gt(0)&a.breadth60.lt(0),'state']='narrowing'
        a.loc[valid&a.cap_return60.gt(0)&a.breadth60.ge(0),'state']='broadening'
        for h in [4,8,12]:
            mins=[]
            for j,end in enumerate(a[f'end{h}']):
                if pd.isna(end):mins.append(np.nan);continue
                k=a.index.get_loc(end);v=a.close.iloc[j+1:k+1]/a.close.iloc[j+1]-1;mins.append(float(v.min()))
            a[f'min_return{h}']=mins
        ans[sym]=a;v=a.dropna(subset=BASE+NEW)
        cover.append({'symbol':sym,'parent_rows':len(a),'new_complete_features':len(v),'first_complete':str(v.index.min().date()) if len(v) else None,'last_complete':str(v.index.max().date()) if len(v) else None})
    return ans,cover,inventory
def block_ix(n,b,rng):
    b=min(b,n);starts=rng.integers(0,n-b+1,size=int(np.ceil(n/b)))
    return (starts[:,None]+np.arange(b)).ravel()[:n]
def mean_ci(values,block):
    rng=np.random.default_rng(20261119+block);arr=np.asarray(values);draw=[arr[block_ix(len(arr),block,rng)].mean() for _ in range(999)]
    p=min(1.,2*min((np.count_nonzero(np.asarray(draw)<=0)+1)/1000,(np.count_nonzero(np.asarray(draw)>=0)+1)/1000))
    return [float(np.quantile(draw,.025)),float(np.quantile(draw,.975)),p]
def holm(p):
    p=np.asarray(p);order=np.argsort(p);x=np.maximum.accumulate(p[order]*np.arange(len(p),0,-1));out=np.empty(len(p));out[order]=np.minimum(x,1);return out
def predict(x,y,xt,alpha):
    mu=x.mean(0);sd=x.std(0);sd=np.where(sd>1e-12,sd,1);z=(x-mu)/sd;ym=y.mean()
    beta=np.linalg.solve(z.T@z+alpha*np.eye(z.shape[1]),z.T@(y-ym))
    return ym+(xt-mu)/sd@beta,{'mean':mu.tolist(),'sd':sd.tolist(),'beta':beta.tolist(),'intercept':float(ym)}
def model_run(ps):
    variants={'base':BASE,'base_breadth':BASE+C['models']['add_breadth'],'base_real':BASE+C['models']['add_real'],'base_joint':BASE+NEW}
    preds=[];fits=[];coverage=[]
    for sym,a in ps.items():
        for h in [4,8,12]:
            b=a.dropna(subset=BASE+NEW+[f'future{h}'])
            for year in C['models']['test_years']:
                cutoff=pd.Timestamp(year,1,1);train=b.loc[(b.index<cutoff)&b[f'end{h}'].lt(cutoff)];test=b.loc[b.index.year==year]
                ok=len(train)>=756 and len(test)>0
                coverage.append({'symbol':sym,'horizon_weeks':h,'year':year,'train_n':len(train),'test_n':len(test),'status':'FIT' if ok else 'INSUFFICIENT_TRAIN_OR_TEST'})
                if not ok:continue
                y=train[f'future{h}'].to_numpy()
                for alpha in C['models']['alpha']:
                    yh={}
                    for m,cols in variants.items():
                        yp,params=predict(train[cols].to_numpy(),y,test[cols].to_numpy(),alpha);yh[m]=yp
                        fits.append({'symbol':sym,'horizon_weeks':h,'year':year,'alpha':alpha,'model':m,'features':cols,'train_n':len(train),'latest_label':str(train[f'end{h}'].max().date()),'params':params})
                    for j,(d,row) in enumerate(test.iterrows()):preds.append({'symbol':sym,'date':str(d.date()),'period':row.period,'horizon_weeks':h,'year':year,'alpha':alpha,'actual':row[f'future{h}'],**{m:float(v[j]) for m,v in yh.items()}})
    pred=pd.DataFrame(preds);summ=[];contrasts=[]
    for (sym,h,alpha),a in pred.groupby(['symbol','horizon_weeks','alpha']):
        for per in ['all']+C['periods']:
            b=a if per=='all' else a.loc[a.period.eq(per)]
            if not len(b):continue
            y=b.actual.to_numpy();base_error=(b.base-y)**2
            for m in variants:
                err=b[m].to_numpy()-y
                summ.append({'symbol':sym,'horizon_weeks':h,'alpha':alpha,'period':per,'model':m,'n':len(b),'MSE':float(np.mean(err**2)),'MAE':float(np.mean(abs(err))),'sign_accuracy':float(np.mean(np.sign(b[m])==np.sign(y)))})
                if m=='base':continue
                delta=err**2-base_error.to_numpy()
                for block in ([126,63,252] if h==8 and per=='all' else [126]):
                    ci=mean_ci(delta,block) if h==8 and per=='all' else [None,None,None]
                    contrasts.append({'symbol':sym,'horizon_weeks':h,'alpha':alpha,'period':per,'model':m,'n':len(b),'block':block,'delta_MSE':float(delta.mean()),'relative_MSE_change':float(delta.mean()/base_error.mean()),'ci_low':ci[0],'ci_high':ci[1],'bootstrap_p':ci[2]})
    out=pd.DataFrame(contrasts);primary=out.horizon_weeks.eq(8)&out.alpha.eq(1)&out.period.eq('all')&out.block.eq(126)
    out.loc[primary,'holm_p']=holm(out.loc[primary,'bootstrap_p'].to_numpy())
    return pred,fits,pd.DataFrame(coverage),pd.DataFrame(summ),out
def states(ps):
    rows=[];contrasts=[]
    for sym,a in ps.items():
        for h in [4,8,12]:
            q=a.dropna(subset=BASE+NEW+[f'future{h}'])
            for per in ['all']+C['periods']:
                b=q if per=='all' else q.loc[q.period.eq(per)]
                for state in ['narrowing','broadening','weak']:
                    x=b.loc[b.state.eq(state)];y=x[f'future{h}']
                    rows.append({'symbol':sym,'horizon_weeks':h,'period':per,'state':state,'n':len(x),'mean_forward_return':y.mean(),'negative_final_fraction':float((y<0).mean()) if len(x) else None,'minus10_close_path_fraction':float(x[f'min_return{h}'].le(-.1).mean()) if len(x) else None})
                n=b.loc[b.state.eq('narrowing'),f'future{h}'];br=b.loc[b.state.eq('broadening'),f'future{h}']
                delta=n.mean()-br.mean();ci=[None,None,None]
                if h==8 and per=='all' and len(n)>0 and len(br)>0:
                    rng=np.random.default_rng(20261119);y=b[f'future{h}'].to_numpy();ns=b.state.eq('narrowing').to_numpy();bs=b.state.eq('broadening').to_numpy();draw=[]
                    for _ in range(999):
                        ix=block_ix(len(b),126,rng)
                        if ns[ix].any() and bs[ix].any():draw.append(float(y[ix][ns[ix]].mean()-y[ix][bs[ix]].mean()))
                    draw=np.asarray(draw);ci=[float(np.quantile(draw,.025)),float(np.quantile(draw,.975)),min(1.,2*min((np.count_nonzero(draw<=0)+1)/(len(draw)+1),(np.count_nonzero(draw>=0)+1)/(len(draw)+1)))]
                contrasts.append({'symbol':sym,'horizon_weeks':h,'period':per,'n_narrowing':len(n),'n_broadening':len(br),'mean_return_narrow_minus_broad':delta,'ci_low':ci[0],'ci_high':ci[1],'bootstrap_p':ci[2]})
    out=pd.DataFrame(contrasts);mask=out.bootstrap_p.notna();out.loc[mask,'holm_p']=holm(out.loc[mask,'bootstrap_p'].to_numpy())
    return pd.DataFrame(rows),out
def main():
    assert not (O/'run_manifest.json').exists(),'Immutable result exists; use new version'
    assert sha(R/'config.json')==json.loads((R/'freeze_receipt.json').read_text())['config_sha256']
    for x in json.loads((R/'input_manifest.json').read_text()):assert sha(R/'raw'/f"{x['series']}.csv")==x['sha256']
    start=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic()
    ps,cov,inventory=panels()
    for sym,a in ps.items():a.to_csv(P/(sym.replace('.','_')+'_panel.csv'),index_label='date')
    (O/'coverage.json').write_text(json.dumps({'markets':cov,'source_pair':inventory},indent=2));print('Aligned source features and saved coverage',flush=True)
    s,sc=states(ps);s.to_csv(O/'state_summary.csv',index=False);sc.to_csv(O/'state_contrasts.csv',index=False)
    pred,fits,coverage,summ,con=model_run(ps)
    pred.to_csv(P/'predictions.csv',index=False);(P/'fits.json').write_text(json.dumps(fits,indent=2));coverage.to_csv(O/'model_coverage.csv',index=False);summ.to_csv(O/'model_summary.csv',index=False);con.to_csv(O/'model_contrasts.csv',index=False)
    m={'run_id':C['run_id'],'started_utc':start,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seconds':time.monotonic()-t,'code_sha256':sha(__file__),'config_sha256':sha(R/'config.json'),'input_manifest_sha256':sha(R/'input_manifest.json'),'parent_run_manifest_sha256':C['parent_run_manifest_sha256'],'prediction_rows':len(pred),'fits':len(fits),'software':{'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__},'outputs':{str(p.relative_to(R)):sha(p) for folder in [O,P] for p in sorted(folder.glob('*')) if p.is_file()},'independent_validation':False}
    (O/'run_manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps({k:v for k,v in m.items() if k!='outputs'}),flush=True)
if __name__=='__main__':main()
