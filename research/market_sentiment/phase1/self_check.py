"""Same-author reconciliation from immutable daily input; not independent validation."""
from pathlib import Path
import argparse, bisect, datetime as dt, hashlib, json, sqlite3, statistics
import numpy as np
import pandas as pd
import baseline

def close(a,b):
    assert np.isclose(float(a),float(b),rtol=1e-10,atol=1e-12),(a,b)

def check(database,results):
    cfg=json.loads(results.parent.joinpath('config.json').read_text())
    assert hashlib.sha256(database.read_bytes()).hexdigest()==cfg['database_sha256']
    panel=pd.read_csv(results/'weekly_panel.csv',keep_default_na=False)
    summary=pd.read_csv(results/'baseline_summary.csv')
    cycles=pd.read_csv(results/'cycles_all_thresholds.csv')
    db=sqlite3.connect('file:'+str(database.resolve())+'?mode=ro',uri=True)
    weekly_checked=feature_checked=outcome_checked=summary_checked=bootstrap_checked=0
    prefix_checks=[]
    for spec in cfg['markets']:
        symbol=spec['symbol'];raw={}
        for day,price in db.execute('select local_date,close from research_daily where symbol=? and autype=? order by local_date',(symbol,spec['autype'])):
            d=dt.date.fromisoformat(day);week=d+dt.timedelta(days=4-d.weekday())
            if week.isoformat()<=cfg['cutoff']:raw[week]=(day,float(price))
        rows=panel[panel.symbol==symbol].copy();weeks=sorted(raw); prices=[raw[t][1] for t in weeks]
        assert list(rows.week_label)==[x.isoformat() for x in weeks]
        gaps=[dt.date.fromisoformat(x[0]) for x in db.execute('select local_date from unresolved_dates where symbol=?',(symbol,))]
        for i,(_,r) in enumerate(rows.iterrows()):
            assert r.actual_date[:10]==raw[weeks[i]][0]
            close(r.close,prices[i]);weekly_checked+=1
            if r.feature_valid not in [True,'True']:continue
            assert i>=52
            assert not any(weeks[i-52]-dt.timedelta(days=4)<=g<=weeks[i] for g in gaps)
            close(r.momentum4,prices[i]/prices[i-4]-1)
            close(r.momentum13,prices[i]/prices[i-13]-1)
            close(r.sma26,statistics.mean(prices[i-25:i+1]))
            close(r.drawdown52,prices[i]/max(prices[i-51:i+1])-1)
            logs=[np.log(prices[k]/prices[k-1]) for k in range(i-12,i+1)]
            close(r.volatility13,statistics.stdev(logs)*np.sqrt(52))
            weak=prices[i]<statistics.mean(prices[i-25:i+1]);m=prices[i]/prices[i-4]-1
            expected=('weak_rebounding' if m>0 else 'weak_falling') if weak else ('strong_rising' if m>=0 else 'strong_fading')
            assert r.state==expected;feature_checked+=1
            for h in cfg['horizons_calendar_weeks']:
                if r[f'outcome_reason_{h}']!='eligible':continue
                assert i+1<len(weeks)
                target=weeks[i+1]+dt.timedelta(weeks=h)
                assert target.isoformat()<=cfg['cutoff']
                j=bisect.bisect_right(weeks,target)-1
                assert (target-weeks[j]).days<=14 and j>i+1
                assert r[f'entry_date_{h}']==raw[weeks[i+1]][0]
                assert r[f'endpoint_date_{h}']==raw[weeks[j]][0]
                close(r[f'return_{h}'],prices[j]/prices[i+1]-1)
                close(r[f'min_path_return_{h}'],min(prices[i+1:j+1])/prices[i+1]-1)
                assert not any(weeks[i]-dt.timedelta(days=4)<=g<=weeks[j] for g in gaps)
                outcome_checked+=1
        # Deliberately corrupt all later prices: earlier feature states must not change.
        w=baseline.weekly_from_daily(db,symbol,spec['autype'],cfg['cutoff'])
        cut=len(w)//2;altered=w.copy();altered.loc[altered.index[cut+1]:,'close']*=7
        got=baseline.annotate(altered,[pd.Timestamp(x) for x in gaps],cfg)
        original_states=rows.state.to_numpy()
        assert list(got.state.iloc[:cut+1])==list(original_states[:cut+1])
        prefix_checks.append({'symbol':symbol,'future_prices_multiplied_by':7,'prefix_rows':cut+1,'earlier_states_unchanged':True})
    # Recompute every conditional sample and mean directly from saved paired rows.
    for _,r in summary.iterrows():
        a=panel[panel.symbol==r.symbol].copy();h=int(r.horizon_calendar_weeks)
        seg=next((s for s in cfg['segments'] if s['name']==r.segment),{'start':'1996-01-01','end':cfg['cutoff']})
        a=a[(a.week_label>=seg['start'])&(a.week_label<=seg['end'])]
        y=pd.to_numeric(a[f'return_{h}'],errors='coerce')
        ok=y.notna()&(a[f'endpoint_date_{h}']<=seg['end'])&(a[f'target_date_{h}']<=seg['end'])
        mask=ok if r.state=='all' else ok&(a.state==r.state)
        vals=y[mask].to_numpy(float)
        assert len(vals)==int(r.signal_weeks) and int(ok.sum())==int(r.eligible_weeks_all)
        if len(vals):
            close(r.mean_price_change,statistics.mean(vals));close(r.median_price_change,statistics.median(vals))
            close(r.positive_fraction,sum(v>0 for v in vals)/len(vals))
            close(r.effect_vs_all,statistics.mean(vals)-float(y[ok].mean()))
        summary_checked+=1
    # Independent aggregation loop checks frozen primary 26-week bootstrap intervals.
    for symbol in ['US.QQQ','SH.000001','SH.000688','US..SOX']:
        seg=cfg['segments'][2];h=8
        a=panel[(panel.symbol==symbol)&(panel.week_label>=seg['start'])&(panel.week_label<=seg['end'])]
        y=pd.to_numeric(a[f'return_{h}'],errors='coerce').to_numpy(float)
        ok=np.isfinite(y)&(a[f'endpoint_date_{h}'].to_numpy()<=seg['end'])&(a[f'target_date_{h}'].to_numpy()<=seg['end'])
        n=len(a);rng=np.random.default_rng(cfg['bootstrap']['seed']);block=26
        starts=rng.integers(0,n,size=(2000,int(np.ceil(n/block))))
        ix=((starts[:,:,None]+np.arange(block))%n).reshape(2000,-1)[:,:n]
        for state in ['weak_rebounding','strong_fading']:
            r=summary[(summary.symbol==symbol)&(summary.segment==seg['name'])&(summary.horizon_calendar_weeks==h)&(summary.state==state)].iloc[0]
            if pd.isna(r.ci95_low_b26):continue
            m=ok&(a.state.to_numpy()==state);effects=[]
            for sampled in ix:
                den=m[sampled].sum();den_all=ok[sampled].sum()
                if den and den_all:effects.append(y[sampled][m[sampled]].mean()-y[sampled][ok[sampled]].mean())
            close(r.ci95_low_b26,np.quantile(effects,.025));close(r.ci95_high_b26,np.quantile(effects,.975));bootstrap_checked+=1
    for _,r in cycles.iterrows():
        a=panel[(panel.symbol==r.symbol)].set_index('week_label')
        close(a.loc[r.extremum_week,'close'],r.extremum_close)
        close(a.loc[r.confirmation_week,'close'],r.confirmation_close)
        assert r.extremum_date<=r.confirmation_date
        if r.type=='trough':assert r.confirmation_close>=r.extremum_close*(1+r.threshold)-1e-9
        else:assert r.confirmation_close<=r.extremum_close*(1-r.threshold)+1e-9
    db.close()
    result={'status':'PASS','validation_type':'same-author raw-input reconciliation and future-perturbation check','independent_validation':False,
        'weekly_prices_checked':weekly_checked,'eligible_feature_rows_checked':feature_checked,'forward_outcomes_checked':outcome_checked,
        'summary_rows_reconciled':summary_checked,'primary_bootstrap_intervals_recomputed':bootstrap_checked,'cycle_rows_checked':len(cycles),
        'future_perturbation_checks':prefix_checks,'input_unchanged':hashlib.sha256(database.read_bytes()).hexdigest()==cfg['database_sha256'],
        'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (results/'self_check.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',type=Path,required=True);p.add_argument('--results',type=Path,required=True);a=p.parse_args();check(a.database,a.results)
