"""Frozen, descriptive price-state study. No strategy fitting or news inference."""
from pathlib import Path
import argparse, datetime as dt, hashlib, json, sqlite3, sys, platform
import numpy as np
import pandas as pd

STATES = ['weak_falling','weak_rebounding','strong_rising','strong_fading']

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,x): Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def runs(mask):
    a=np.asarray(mask,dtype=bool)
    return int(np.sum(a & ~np.r_[False,a[:-1]]))

def weekly_from_daily(db,symbol,autype,cutoff):
    d=pd.read_sql_query('SELECT * FROM research_daily WHERE symbol=? AND autype=? ORDER BY local_date',db,params=(symbol,autype))
    d['actual_date']=pd.to_datetime(d.local_date)
    d['week']=d.actual_date + pd.to_timedelta(4-d.actual_date.dt.weekday,unit='D')
    d=d[d.week<=pd.Timestamp(cutoff)]
    w=d.groupby('week',sort=True).tail(1).copy().set_index('week')
    w['n_daily']=d.groupby('week').size()
    return w

def known_gap(gaps,start,end):
    return any(start<=x<=end for x in gaps)

def annotate(w,gaps,cfg):
    w=w.copy(); c=w.close.astype(float)
    w['momentum4']=c/c.shift(4)-1; w['momentum13']=c/c.shift(13)-1
    w['sma26']=c.rolling(26,min_periods=26).mean()
    w['drawdown52']=c/c.rolling(52,min_periods=52).max()-1
    w['volatility13']=np.log(c/c.shift(1)).rolling(13,min_periods=13).std(ddof=1)*np.sqrt(52)
    w['state']=''; w['feature_valid']=False; w['exclusion_reason']='warmup_53_observed_weeks'
    w['feature_window_start']=''
    for i in range(52,len(w)):
        start=w.index[i-52]-pd.Timedelta(days=4); end=w.index[i]
        reason=''
        if known_gap(gaps,start,end): reason='known_provider_gap_in_features'
        elif w.index[i-52:i+1].to_series().diff().dt.days.max()>21: reason='observed_week_gap_over_21_days'
        w.loc[end,'feature_window_start']=start.date().isoformat()
        w.loc[end,'exclusion_reason']=reason
        if reason: continue
        w.loc[end,'feature_valid']=True
        weak=c.iloc[i]<w.sma26.iloc[i]; m=w.momentum4.iloc[i]
        state=('weak_rebounding' if m>0 else 'weak_falling') if weak else ('strong_rising' if m>=0 else 'strong_fading')
        w.loc[end,'state']=state
    cutoff=pd.Timestamp(cfg['cutoff'])
    for h in cfg['horizons_calendar_weeks']:
        for name in ['return','min_path_return']: w[f'{name}_{h}']=np.nan
        for name in ['entry_date','endpoint_date','target_date','entry_week','endpoint_week']: w[f'{name}_{h}']=''
        w[f'outcome_reason_{h}']='not_evaluated'
        for i in range(len(w)):
            t=w.index[i]
            if not w.feature_valid.iloc[i]:
                w.loc[t,f'outcome_reason_{h}']='invalid_features'; continue
            if i+1>=len(w):
                w.loc[t,f'outcome_reason_{h}']='right_censored'; continue
            entry=i+1; target=w.index[entry]+pd.Timedelta(weeks=h)
            w.loc[t,f'target_date_{h}']=target.date().isoformat()
            if target>cutoff:
                w.loc[t,f'outcome_reason_{h}']='right_censored'; continue
            j=int(w.index.searchsorted(target,side='right')-1)
            if j<=entry or (target-w.index[j]).days>14:
                w.loc[t,f'outcome_reason_{h}']='endpoint_missing_or_stale'; continue
            if known_gap(gaps,t-pd.Timedelta(days=4),w.index[j]):
                w.loc[t,f'outcome_reason_{h}']='known_provider_gap_in_outcome'; continue
            if w.index[i:j+1].to_series().diff().dt.days.max()>21:
                w.loc[t,f'outcome_reason_{h}']='observed_week_gap_over_21_days'; continue
            w.loc[t,f'return_{h}']=c.iloc[j]/c.iloc[entry]-1
            w.loc[t,f'min_path_return_{h}']=c.iloc[entry:j+1].min()/c.iloc[entry]-1
            for name,value in [('entry_date',w.actual_date.iloc[entry]),('endpoint_date',w.actual_date.iloc[j]),('entry_week',w.index[entry]),('endpoint_week',w.index[j])]:
                w.loc[t,f'{name}_{h}']=value.date().isoformat()
            w.loc[t,f'outcome_reason_{h}']='eligible'
    return w

def cycles(w,gaps,threshold):
    events=[]; trend=0; low=high=None; lowi=highi=None; reset=0
    for i,(t,row) in enumerate(w.iterrows()):
        if i and (known_gap(gaps,w.index[i-1]-pd.Timedelta(days=4),t) or (t-w.index[i-1]).days>21):
            trend=0;low=high=None;reset+=1
        v=float(row.close)
        if low is None: low=high=v;lowi=highi=i;continue
        if trend==0:
            if v<low:low=v;lowi=i
            if v>high:high=v;highi=i
            if v>=low*(1+threshold):
                kind='trough'; k=lowi; extreme=low;trend=1;high=v;highi=i
            elif v<=high*(1-threshold):
                kind='peak';k=highi;extreme=high;trend=-1;low=v;lowi=i
            else:continue
        elif trend==1:
            if v>high:high=v;highi=i
            if v>high*(1-threshold):continue
            kind='peak';k=highi;extreme=high;trend=-1;low=v;lowi=i
        else:
            if v<low:low=v;lowi=i
            if v<low*(1+threshold):continue
            kind='trough';k=lowi;extreme=low;trend=1;high=v;highi=i
        events.append({'threshold':threshold,'type':kind,'extremum_week':w.index[k].date().isoformat(),
          'extremum_date':w.actual_date.iloc[k].date().isoformat(),'extremum_close':extreme,
          'confirmation_week':t.date().isoformat(),'confirmation_date':row.actual_date.date().isoformat(),
          'confirmation_close':v,'confirmation_lag_days':int((row.actual_date-w.actual_date.iloc[k]).days),
          'contiguous_price_segment':reset,'status':'RETROSPECTIVE_EXTREMUM_LATER_CONFIRMED'})
    return events

def summaries(w,cfg,symbol):
    records=[]; contrasts=[]
    segs=[{'name':'full_descriptive','start':'1996-01-01','end':cfg['cutoff']}]+cfg['segments']
    boot=cfg['bootstrap']
    for seg in segs:
      a=w[(w.index>=seg['start'])&(w.index<=seg['end'])].copy()
      if a.empty:continue
      for h in cfg['horizons_calendar_weeks']:
        y=a[f'return_{h}'].to_numpy(float)
        end=pd.to_datetime(a[f'endpoint_date_{h}'],errors='coerce')
        target=pd.to_datetime(a[f'target_date_{h}'],errors='coerce')
        eligible=np.isfinite(y)&(end<=pd.Timestamp(seg['end'])).to_numpy()&(target<=pd.Timestamp(seg['end'])).to_numpy()
        y=np.where(eligible,y,np.nan)
        if not eligible.any():continue
        baseline=float(np.nanmean(y)); state=a.state.to_numpy()
        masks={'all':eligible,**{s:eligible&(state==s) for s in STATES}}
        dist={}; contrast_dist={}
        for block in [boot['primary_block_observed_weeks']]+boot['sensitivity_block_observed_weeks']:
          rng=np.random.default_rng(boot['seed']); n=len(a);b=boot['replications']
          starts=rng.integers(0,n,size=(b,int(np.ceil(n/block))))
          ix=((starts[:,:,None]+np.arange(block))%n).reshape(b,-1)[:,:n]
          sampled_y=y[ix]; means={}
          for s,m in masks.items():
            sm=m[ix];den=sm.sum(axis=1)
            means[s]=np.divide(np.where(sm,sampled_y,0).sum(axis=1),den,out=np.full(b,np.nan),where=den>0)
            dist[s,block]=means[s]-means['all'] if s!='all' else np.zeros(b)
          for left,right in [('weak_rebounding','weak_falling'),('strong_fading','strong_rising')]:
            contrast_dist[left,right,block]=means[left]-means[right]
        for s,m in masks.items():
          vals=y[m]; count=int(m.sum()); episodes=runs(m)
          r={'symbol':symbol,'segment':seg['name'],'horizon_calendar_weeks':h,'state':s,
             'eligible_weeks_all':int(eligible.sum()),'signal_weeks':count,'contiguous_signal_runs':episodes,
             'mean_price_change':float(vals.mean()) if count else None,'median_price_change':float(np.median(vals)) if count else None,
             'positive_fraction':float((vals>0).mean()) if count else None,'p10_price_change':float(np.quantile(vals,.1)) if count else None,
             'mean_min_path_return':float(a.loc[m,f'min_path_return_{h}'].mean()) if count else None,
             'effect_vs_all':float(vals.mean()-baseline) if count else None,
             'interval_status':'DESCRIPTIVE_ONLY' if count>=20 and episodes>=5 else 'UNDERPOWERED'}
          for block in [26,13,52]:
            v=dist[s,block];v=v[np.isfinite(v)]
            good=count>=boot['min_signal_weeks_for_interval'] and episodes>=boot['min_contiguous_runs_for_interval'] and len(v)>=1900
            r[f'ci95_low_b{block}']=float(np.quantile(v,.025)) if good else None
            r[f'ci95_high_b{block}']=float(np.quantile(v,.975)) if good else None
            r[f'bootstrap_valid_b{block}']=len(v)
          records.append(r)
        for left,right in [('weak_rebounding','weak_falling'),('strong_fading','strong_rising')]:
          ml,mr=masks[left],masks[right]
          r={'symbol':symbol,'segment':seg['name'],'horizon_calendar_weeks':h,'left':left,'right':right,
             'left_n':int(ml.sum()),'right_n':int(mr.sum()),'left_runs':runs(ml),'right_runs':runs(mr),
             'mean_difference':float(np.nanmean(y[ml])-np.nanmean(y[mr])) if ml.any() and mr.any() else None}
          for block in [26,13,52]:
            v=contrast_dist[left,right,block];v=v[np.isfinite(v)]
            good=min(ml.sum(),mr.sum())>=20 and min(runs(ml),runs(mr))>=5 and len(v)>=1900
            r[f'ci95_low_b{block}']=float(np.quantile(v,.025)) if good else None
            r[f'ci95_high_b{block}']=float(np.quantile(v,.975)) if good else None
          contrasts.append(r)
    return records,contrasts

def run(database,out,config):
    cfg=json.loads(config.read_text()); frozen=json.loads(config.with_name('freeze_receipt.json').read_text())
    assert sha(config)==frozen['config.json']
    assert sha(database)==cfg['database_sha256']
    out.mkdir(exist_ok=False,parents=True)
    db=sqlite3.connect('file:'+str(database.resolve())+'?mode=ro',uri=True)
    all_w=[];events=[];stats=[];contrasts=[];coverage=[]
    for spec in cfg['markets']:
        s=spec['symbol'];gaps=[pd.Timestamp(x[0]) for x in db.execute('select local_date from unresolved_dates where symbol=?',(s,))]
        w=annotate(weekly_from_daily(db,s,spec['autype'],cfg['cutoff']),gaps,cfg)
        w['symbol']=s;w['autype']=spec['autype'];all_w.append(w)
        for q in cfg['cycle_thresholds']:
            events.extend([{'symbol':s,**x} for x in cycles(w,gaps,q)])
        r,k=summaries(w,cfg,s);stats+=r;contrasts+=k
        coverage.append({'symbol':s,'weekly_observations':len(w),'feature_eligible_weeks':int(w.feature_valid.sum()),
           'first_week':w.index[0].date().isoformat(),'last_week':w.index[-1].date().isoformat(),
           'first_valid_feature_week':w.index[w.feature_valid][0].date().isoformat(),
           'known_gap_invalid_feature_weeks':int((w.exclusion_reason=='known_provider_gap_in_features').sum())})
    db.close()
    pd.concat(all_w).rename_axis('week_label').reset_index().to_csv(out/'weekly_panel.csv',index=False)
    pd.DataFrame(stats).to_csv(out/'baseline_summary.csv',index=False)
    pd.DataFrame(contrasts).to_csv(out/'state_contrasts.csv',index=False)
    pd.DataFrame(events).to_csv(out/'cycles_all_thresholds.csv',index=False)
    dump(out/'coverage.json',coverage)
    manifest={'run_id':'price-state-phase1-20261119-v1','started_from_frozen_config':True,
       'timestamp_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'command':sys.argv,
       'database_sha256':sha(database),'configuration_sha256':sha(config),'code_sha256':sha(__file__),
       'python':sys.version,'numpy':np.__version__,'pandas':pd.__version__,'platform':platform.platform(),
       'fitting_performed':False,'seed':cfg['bootstrap']['seed'],'seed_scope':'bootstrap only',
       'outputs':{p.name:sha(p) for p in sorted(out.iterdir())},'input_unchanged':sha(database)==cfg['database_sha256'],
       'status':'EXECUTED_DESCRIPTIVE_PRICE_ANALYSIS_NOT_SENTIMENT_VALIDATION'}
    dump(out/'run_manifest.json',manifest)
    print(json.dumps({'status':manifest['status'],'coverage':coverage,'summary_rows':len(stats),'contrast_rows':len(contrasts),'cycle_rows':len(events)}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--config',type=Path,default=Path(__file__).with_name('config.json'));a=p.parse_args()
    run(a.database,a.out,a.config)
