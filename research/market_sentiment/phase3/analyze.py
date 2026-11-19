"""Frozen phase-3 census and current-vintage chronological proxy diagnostics.

Raw market panels and model predictions stay private; aggregate tables and
dates-only event annotations are eligible for research publication.
"""
from pathlib import Path
import datetime, hashlib, json, platform, sqlite3, time
import numpy as np
import pandas as pd
from scipy.stats import rankdata

R=Path(__file__).resolve().parent
C=json.loads((R/'config.json').read_text())
DB=R.parent/'moomoo_research_dataset_20261119/dataset_v0_1/market_research.sqlite'
OUT=R/'results_v1_1'; PRIVATE=R/'private_v1_1'
AMENDMENT=json.loads((R/'AMENDMENT_v1_1.json').read_text())
OUT.mkdir(exist_ok=True);PRIVATE.mkdir(exist_ok=True)
FEATURES=C['models']['price_features']
VF=C['models']['vix_features']; FF=C['models']['funding_features']
PROXIES=C['associations']['all_proxies']

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def period(d):
    d=str(pd.Timestamp(d).date())
    for k,(a,b) in C['periods'].items():
        if a<=d<=b:return k
    return 'outside'

def source_features():
    ans={}
    for sid in ['VIXCLS','BAA10Y','DGS10']:
        a=pd.read_csv(R/'raw'/f'{sid}.csv',index_col=0,parse_dates=True)
        a=a.apply(pd.to_numeric,errors='coerce').dropna()
        s=a.iloc[:,0]; f=pd.DataFrame(index=a.index)
        f['source_date']=a.index
        if sid=='VIXCLS':
            f['vix_level']=s
            f['risk_appetite']=100*(1-s.rolling(1260,min_periods=756).rank(pct=True))
            f['vix_change20']=s/s.shift(20)-1
            mx=s.rolling(21).max();mn=s.rolling(21).min()
            f['risk_easing']=((mx>=30)&(s<=.8*mx)).astype(float)
            f['risk_break']=((mn<=15)&(s>=1.5*mn)).astype(float)
        elif sid=='BAA10Y':
            f['credit_level']=s;f['credit_change20']=s-s.shift(20)
        else:f['rate_change20']=s-s.shift(20)
        ans[sid]=f
    return ans

def align_sources(dates,fs):
    # Strictly-before then one further observation back. Always retains actual
    # selected date to audit staleness; never uses a future daily close.
    ans=pd.DataFrame(index=dates)
    for sid,f in fs.items():
        jj=f.index.searchsorted(dates,side='left')-2
        safe=np.maximum(jj,0)
        age=(dates-f.index[safe]).days
        valid=(jj>=0)&(age<=7)
        for col in f:
            name=sid+'_date' if col=='source_date' else col
            if col=='source_date':
                v=f.index[safe].to_numpy().copy();v[~valid]=np.datetime64('NaT')
            else:
                v=f[col].iloc[safe].to_numpy(dtype=float).copy();v[~valid]=np.nan
            ans[name]=v
    return ans

def load_panels():
    fs=source_features();panels={};cover=[]
    with sqlite3.connect(f'file:{DB}?mode=ro',uri=True) as con:
        for sym,autype in C['symbols_autype'].items():
            a=pd.read_sql_query('SELECT local_date, close FROM research_daily WHERE symbol=? AND autype=? ORDER BY local_date',con,params=[sym,autype],parse_dates=['local_date']).set_index('local_date')
            a=a.loc[C['start']:C['end']].copy()
            assert a.index.is_unique and a['close'].gt(0).all()
            holes=pd.DatetimeIndex(C['known_price_holes'].get(sym,[]))
            seg=np.zeros(len(a),dtype=int)
            for i in range(1,len(a)):
                seg[i]=seg[i-1]+int(((holes>a.index[i-1])&(holes<a.index[i])).any())
            a['segment']=seg;a['row_i']=np.arange(len(a));a['period']=[period(x) for x in a.index]
            for _,b in a.groupby('segment'):
                p=b.close;r=p.pct_change(fill_method=None)
                a.loc[b.index,'return20']=p/p.shift(20)-1
                a.loc[b.index,'return60']=p/p.shift(60)-1
                a.loc[b.index,'ma60_gap']=p/p.rolling(60).mean()-1
                a.loc[b.index,'drawdown252']=p/p.rolling(252).max()-1
                a.loc[b.index,'volatility20']=r.rolling(20).std(ddof=1)*np.sqrt(252)
            a=a.join(align_sources(a.index,fs))
            a['price_up']=(a.ma60_gap>0)&(a.return20>0)
            a['price_down']=(a.ma60_gap<0)&(a.return20<0)
            a['joint_up']=a.price_up&a.risk_easing.eq(1)
            a['joint_down']=a.price_down&a.risk_break.eq(1)
            for name in ['risk_easing','risk_break']:a[name]=a[name].eq(1)
            for h in C['outcomes']['horizons_calendar_weeks']:
                ends=[];starts=[];returns=[]
                for i in range(len(a)):
                    j=i+1
                    if j>=len(a):starts.append(pd.NaT);ends.append(pd.NaT);returns.append(np.nan);continue
                    target=a.index[j]+pd.Timedelta(weeks=h)
                    k=int(a.index.searchsorted(target,side='right')-1)
                    ok=(target<=a.index[-1] and k>j and a.segment.iloc[k]==a.segment.iloc[i] and (target-a.index[k]).days<=7 and period(a.index[k])==a.period.iloc[i] and period(a.index[j])==a.period.iloc[i])
                    starts.append(a.index[j]);ends.append(a.index[k] if ok else pd.NaT)
                    returns.append(float(a.close.iloc[k]/a.close.iloc[j]-1) if ok else np.nan)
                a[f'entry{h}']=starts;a[f'end{h}']=ends;a[f'future{h}']=returns
            panels[sym]=a
            cover.append({'symbol':sym,'autype':autype,'rows':len(a),'first':str(a.index[0].date()),'last':str(a.index[-1].date()),'segments':int(a.segment.nunique()),'known_holes':len(holes),'complete_feature_rows':int(a[FEATURES+VF+FF].notna().all(axis=1).sum()),**{f'outcome{h}_rows':int(a[f'future{h}'].notna().sum()) for h in [4,8,12]}})
    return panels,cover

def census_one(b,q):
    """Sequential close directional change; extrema are explicitly hindsight."""
    prices=b.close.to_numpy();n=len(b);events=[];hi=lo=0;direction=0;current=None
    for i in range(1,n):
        p=prices[i]
        if direction==0:
            if p>prices[hi]:hi=i
            if p<prices[lo]:lo=i
            if p>=prices[lo]*(1+q):direction=1;origin=lo;hi=i
            elif p<=prices[hi]*(1-q):direction=-1;origin=hi;lo=i
            else:continue
            current={'direction':direction,'origin':origin,'confirm':i}
            events.append(current)
        elif direction==1:
            if p>prices[hi]:hi=i
            if p<=prices[hi]*(1-q):
                current.update(end=hi,reversal_confirm=i,complete=True)
                direction=-1;lo=i;current={'direction':-1,'origin':hi,'confirm':i};events.append(current)
        else:
            if p<prices[lo]:lo=i
            if p>=prices[lo]*(1+q):
                current.update(end=lo,reversal_confirm=i,complete=True)
                direction=1;hi=i;current={'direction':1,'origin':lo,'confirm':i};events.append(current)
    if current is not None:current.update(end=hi if direction==1 else lo,reversal_confirm=None,complete=False)
    return events,{'direction':direction,'last_date':str(b.index[-1].date()),'initial_neutral':not bool(events),'right_censored':True}

RULES=['price_up','price_down','risk_easing','risk_break','joint_up','joint_down']
def onsets(a,rule):
    rows=[]
    for _,b in a.groupby('segment'):
        last=-10**9;prior=None
        for j,(d,row) in enumerate(b.iterrows()):
            cols=['return20','ma60_gap'] if rule.startswith('price') else ['VIXCLS_date'] if rule.startswith('risk') else ['return20','ma60_gap','VIXCLS_date']
            if row[cols].isna().any():prior=None;continue
            now=bool(row[rule])
            if now and prior is False and j-last>=20:rows.append(d);last=j
            prior=now
    return pd.DatetimeIndex(rows)

def census(panels):
    rows=[];tails=[]
    for sym,a in panels.items():
        onset_map={rule:onsets(a,rule) for rule in RULES}
        for q in C['directional_change_thresholds']:
            for seg,b in a.groupby('segment'):
                ev,tail=census_one(b,q);tail.update(symbol=sym,threshold=q,segment=int(seg));tails.append(tail)
                for j,e in enumerate(ev):
                    o=b.index[e['origin']];c=b.index[e['confirm']];end=b.index[e['end']]
                    prev=b.iloc[e['origin']-1] if e['origin']>0 else None
                    x={'event_id':f'{sym}_{int(q*100)}_{seg}_{j+1:03d}','symbol':sym,'threshold':q,'segment':int(seg),'direction':'UP' if e['direction']==1 else 'DOWN','origin_extreme_date_hindsight':str(o.date()),'threshold_confirmation_date':str(c.date()),'end_extreme_date_hindsight':str(end.date()),'next_reversal_confirmation_date':str(b.index[e['reversal_confirm']].date()) if e['reversal_confirm'] is not None else None,'complete':e['complete'],'confirmation_lag_calendar_days':int((c-o).days),'midterm_confirmation':bool((c-o).days<=182),'extreme_to_extreme_return':float(b.close.iloc[e['end']]/b.close.iloc[e['origin']]-1),'period_origin':period(o),'origin_pre_risk_appetite':None if prev is None or pd.isna(prev.risk_appetite) else float(prev.risk_appetite),'origin_pre_price_up':None if prev is None else bool(prev.price_up),'origin_pre_price_down':None if prev is None else bool(prev.price_down),'attribution_status':'NOT_CAUSALLY_ATTRIBUTED'}
                    for rule,ds in onset_map.items():
                        pos=a.index.get_loc(o);lower=a.index[max(0,pos-20)];upper=a.index[min(len(a)-1,pos+20)]
                        ds=ds[a.loc[ds,'segment'].eq(seg).to_numpy()]
                        x[rule+'_pre20_onsets']='|'.join(str(d.date()) for d in ds[(ds>=lower)&(ds<o)])
                        x[rule+'_post20_onsets']='|'.join(str(d.date()) for d in ds[(ds>=o)&(ds<=upper)])
                    rows.append(x)
    return pd.DataFrame(rows),pd.DataFrame(tails)

def alerts(panels):
    records=[];summ=[]
    for sym,a in panels.items():
        for rule in RULES:
            direction=1 if rule in ['price_up','risk_easing','joint_up'] else -1
            for d in onsets(a,rule):
                i=a.index.get_loc(d)
                for h in [4,8,12]:
                    end=a.loc[d,f'end{h}'];future=a.loc[d,f'future{h}']
                    for barrier in [.10,.05,.15]:
                        status='CENSORED';hit_date=None
                        if pd.notna(future):
                            j=i+1;k=a.index.get_loc(end)
                            rr=a.close.iloc[j:k+1].to_numpy()/a.close.iloc[j]-1
                            desired=np.flatnonzero(rr>=barrier) if direction==1 else np.flatnonzero(rr<=-barrier)
                            opposite=np.flatnonzero(rr<=-barrier) if direction==1 else np.flatnonzero(rr>=barrier)
                            jd=int(desired[0]) if len(desired) else len(rr)+1
                            jo=int(opposite[0]) if len(opposite) else len(rr)+1
                            if jd<jo:status='DESIRED_FIRST';hit_date=str(a.index[j+jd].date())
                            elif jo<jd:status='OPPOSITE_FIRST';hit_date=str(a.index[j+jo].date())
                            else:status='NO_BARRIER'
                        records.append({'symbol':sym,'rule':rule,'signal_date':str(d.date()),'direction':direction,'period':a.loc[d,'period'],'horizon_weeks':h,'barrier':barrier,'future_return':future,'status':status,'hit_date':hit_date})
    full=pd.DataFrame(records)
    for key,b in full.groupby(['symbol','rule','horizon_weeks','barrier','period'],dropna=False):
        sym,rule,h,barrier,per=key;a=panels[sym];f=b.future_return.dropna();direction=b.direction.iloc[0]
        matched='price_up' if direction==1 else 'price_down'
        base=a.loc[a.period.eq(per)&a[matched],f'future{h}'].dropna()
        summ.append(dict(zip(['symbol','rule','horizon_weeks','barrier','period'],key))|{'alerts':len(b),'mature':len(f),'censored':int(b.status.eq('CENSORED').sum()),'desired_first':int(b.status.eq('DESIRED_FIRST').sum()),'opposite_first':int(b.status.eq('OPPOSITE_FIRST').sum()),'no_barrier':int(b.status.eq('NO_BARRIER').sum()),'desired_first_fraction_all_mature':float(b.status.eq('DESIRED_FIRST').sum()/len(f)) if len(f) else None,'mean_forward_return':f.mean(),'final_direction_fraction':float((direction*f>0).mean()) if len(f) else None,'price_state_comparator':matched,'comparator_daily_rows':len(base),'comparator_mean_return':base.mean(),'difference_vs_price_state_mean':f.mean()-base.mean()})
    return full,pd.DataFrame(summ)

def sample_blocks(n,block,rng):
    length=min(n,block);starts=rng.integers(0,n-length+1,size=int(np.ceil(n/length)))
    return (starts[:,None]+np.arange(length)).ravel()[:n]

def ci_mean(values,block,seed):
    values=np.asarray(values);n=len(values)
    if n<2:return [None,None,None]
    rng=np.random.default_rng(seed);draw=[]
    for _ in range(999):draw.append(float(values[sample_blocks(n,block,rng)].mean()))
    p=min(1.,2*min((np.count_nonzero(np.asarray(draw)<=0)+1)/1000,(np.count_nonzero(np.asarray(draw)>=0)+1)/1000))
    return [float(np.quantile(draw,.025)),float(np.quantile(draw,.975)),p]

def pearson(x,y):
    return float(np.corrcoef(x,y)[0,1]) if len(x)>2 and np.std(x)>0 and np.std(y)>0 else np.nan

def associations(panels):
    rows=[]
    for si,(sym,a) in enumerate(panels.items()):
        for pi,proxy in enumerate(PROXIES):
            for h in [4,8,12]:
                for per in list(C['periods'])+['all']:
                    b=a if per=='all' else a.loc[a.period.eq(per)]
                    b=b[[proxy,f'future{h}']].dropna();n=len(b)
                    x=b[proxy].to_numpy();y=b[f'future{h}'].to_numpy()
                    row={'symbol':sym,'proxy':proxy,'horizon_weeks':h,'period':per,'n':n,'pearson':pearson(x,y),'spearman':pearson(rankdata(x),rankdata(y)),'ci_low':None,'ci_high':None,'bootstrap_p':None,'block_sessions':126,'status':'DESCRIPTIVE'}
                    if h==8 and per=='all' and n>=252:
                        rng=np.random.default_rng(20261119+si*10+pi);draw=[]
                        for _ in range(999):
                            ix=sample_blocks(n,126,rng);draw.append(pearson(rankdata(x[ix]),rankdata(y[ix])))
                        draw=np.asarray(draw);row.update(ci_low=float(np.nanquantile(draw,.025)),ci_high=float(np.nanquantile(draw,.975)),bootstrap_p=min(1.,2*min((np.count_nonzero(draw<=0)+1)/1000,(np.count_nonzero(draw>=0)+1)/1000)),status='EXPLORATORY_CURRENT_VINTAGE')
                    rows.append(row)
    out=pd.DataFrame(rows);mask=out.bootstrap_p.notna();out.loc[mask,'holm_adjusted_p']=holm(out.loc[mask,'bootstrap_p'].to_numpy())
    return out

def holm(p):
    p=np.asarray(p);order=np.argsort(p);s=np.maximum.accumulate(p[order]*np.arange(len(p),0,-1));ans=np.empty(len(p));ans[order]=np.minimum(1,s);return ans

def fit_predict(x,y,xt,alpha=1.):
    mu=x.mean(axis=0);sd=x.std(axis=0);sd=np.where(sd>1e-12,sd,1.)
    z=(x-mu)/sd;zt=(xt-mu)/sd;ym=y.mean()
    beta=np.linalg.solve(z.T@z+alpha*np.eye(z.shape[1]),z.T@(y-ym))
    return ym+zt@beta,{'mean':mu.tolist(),'sd':sd.tolist(),'beta':beta.tolist(),'intercept':float(ym)}

def models(panels):
    preds=[];fits=[];attempts=[]
    variants={'price':FEATURES,'price_vix':FEATURES+VF,'price_funding':FEATURES+FF,'price_both':FEATURES+VF+FF}
    for sym,a in panels.items():
        for h in [4,8,12]:
            b=a.dropna(subset=FEATURES+VF+FF+[f'future{h}']).copy()
            for year in C['models']['test_years']:
                cutoff=pd.Timestamp(year=year,month=1,day=1)
                train=b.loc[(b.index<cutoff)&(b[f'end{h}']<cutoff)]
                test=b.loc[b.index.year==year]
                ok=len(train)>=756 and len(test)>0
                attempts.append({'symbol':sym,'horizon_weeks':h,'year':year,'train_rows':len(train),'test_rows':len(test),'status':'FIT' if ok else 'INSUFFICIENT_TRAIN_OR_TEST'})
                if not ok:continue
                y=train[f'future{h}'].to_numpy()
                yhat={'train_mean':np.repeat(y.mean(),len(test))}
                for name,cols in variants.items():
                    yp,params=fit_predict(train[cols].to_numpy(),y,test[cols].to_numpy())
                    yhat[name]=yp
                    fits.append({'symbol':sym,'horizon_weeks':h,'year':year,'model':name,'features':cols,'train_rows':len(train),'train_first':str(train.index[0].date()),'train_last':str(train.index[-1].date()),'latest_train_label_date':str(train[f'end{h}'].max().date()),'test_first':str(test.index[0].date()),'test_last':str(test.index[-1].date()),'params':params})
                for j,(d,row) in enumerate(test.iterrows()):
                    preds.append({'symbol':sym,'horizon_weeks':h,'year':year,'date':str(d.date()),'period':row.period,'actual_return':row[f'future{h}'],**{name:float(v[j]) for name,v in yhat.items()}})
    p=pd.DataFrame(preds);summ=[];contrasts=[]
    for (sym,h),a in p.groupby(['symbol','horizon_weeks']):
        for per in ['all']+list(C['periods']):
            b=a if per=='all' else a.loc[a.period.eq(per)]
            if not len(b):continue
            y=b.actual_return.to_numpy()
            for m in ['train_mean']+list(variants):
                err=b[m].to_numpy()-y
                summ.append({'symbol':sym,'horizon_weeks':h,'period':per,'model':m,'n':len(b),'MSE':float(np.mean(err**2)),'MAE':float(np.mean(abs(err))),'sign_accuracy':float(np.mean(np.sign(b[m])==np.sign(y))),'first':b.date.min(),'last':b.date.max()})
                if m in ['price_vix','price_funding','price_both']:
                    delta=err**2-(b.price.to_numpy()-y)**2
                    for block in ([126,63,252] if h==8 and per=='all' else [126]):
                        ci=ci_mean(delta,block,20261119+int(h)+block) if h==8 and per=='all' else [None,None,None]
                        contrasts.append({'symbol':sym,'horizon_weeks':h,'period':per,'model':m,'n':len(b),'block_sessions':block,'delta_MSE_vs_price':float(delta.mean()),'relative_MSE_change':float(delta.mean()/np.mean((b.price.to_numpy()-y)**2)),'ci_low':ci[0],'ci_high':ci[1],'bootstrap_p':ci[2],'interpretation':'negative favors added proxy; exploratory current-vintage'})
    contrasts=pd.DataFrame(contrasts);mask=contrasts.horizon_weeks.eq(8)&contrasts.period.eq('all')&contrasts.block_sessions.eq(126)
    contrasts.loc[mask,'holm_adjusted_p']=holm(contrasts.loc[mask,'bootstrap_p'].to_numpy())
    return p,pd.DataFrame(summ),contrasts,fits,pd.DataFrame(attempts)

def main():
    assert sha(DB)==C['price_database_sha256']
    receipt=json.loads((R/'freeze_receipt.json').read_text())
    assert sha(R/'config.json')==receipt['config_sha256']
    for x in json.loads((R/'raw/input_manifest.json').read_text()):assert sha(R/'raw'/f"{x['series']}.csv")==x['sha256']
    assert not (OUT/'run_manifest.json').exists(),'Keep completed run immutable; create new version for a changed analysis'
    started=datetime.datetime.now(datetime.timezone.utc).isoformat();tick=time.monotonic()
    panels,coverage=load_panels();print('Loaded accepted price panels and aligned source dates',flush=True)
    for sym,a in panels.items():a.to_csv(PRIVATE/(sym.replace('.','_')+'_panel.csv'),index_label='date')
    ev,tails=census(panels);ev.to_csv(OUT/'events_all_thresholds.csv',index=False);tails.to_csv(OUT/'open_segments.csv',index=False)
    ev.groupby(['symbol','threshold','direction','complete','midterm_confirmation']).size().rename('events').reset_index().to_csv(OUT/'event_counts.csv',index=False)
    print('Enumerated all directional-change episodes including open tails',flush=True)
    full,alert_summary=alerts(panels);full.to_csv(PRIVATE/'alert_outcomes.csv',index=False);alert_summary.to_csv(OUT/'alert_summary.csv',index=False)
    print('Scored all candidate alerts, censored and no-barrier cases retained',flush=True)
    assoc=associations(panels);assoc.to_csv(OUT/'associations.csv',index=False)
    print('Computed all associations and primary block intervals',flush=True)
    predictions,summary,contrasts,fits,attempts=models(panels)
    predictions.to_csv(PRIVATE/'model_predictions.csv',index=False);(PRIVATE/'model_fit_parameters.json').write_text(json.dumps(fits,indent=2))
    summary.to_csv(OUT/'model_summary.csv',index=False);contrasts.to_csv(OUT/'model_contrasts.csv',index=False);attempts.to_csv(OUT/'model_fit_coverage.csv',index=False)
    (OUT/'coverage.json').write_text(json.dumps(coverage,indent=2))
    manifest={'run_id':AMENDMENT['revision_run_id'],'started_utc':started,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'duration_seconds':time.monotonic()-tick,'code_sha256':sha(__file__),'amendment_sha256':sha(R/'AMENDMENT_v1_1.json'),'config_sha256':sha(R/'config.json'),'input_manifest_sha256':sha(R/'raw/input_manifest.json'),'price_database_sha256':sha(DB),'software':{'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__},'commands_actually_run':['python sentiment_phase3_20261119/analyze.py'],'events':len(ev),'confirmed_event_legs_including_right_censored':len(ev),'alert_outcome_cells':len(full),'fit_count':len(fits),'prediction_rows':len(predictions),'raw_and_result_hashes':{str(p.relative_to(R)):sha(p) for folder in [OUT,PRIVATE] for p in sorted(folder.glob('*')) if p.is_file()},'independent_validation':False}
    (OUT/'run_manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({k:v for k,v in manifest.items() if k!='raw_and_result_hashes'}),flush=True)

if __name__=='__main__':main()
