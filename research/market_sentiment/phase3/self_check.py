"""Same-author cross-check from immutable inputs, not independent validation."""
from pathlib import Path
import datetime,hashlib,json,sqlite3
import numpy as np
import pandas as pd
import analyze as impl

R=Path(__file__).resolve().parent;O=R/'results_v1_1';P=R/'private_v1_1'
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    checks={};m=json.loads((O/'run_manifest.json').read_text());assert digest(R/'analyze.py')==m['code_sha256']
    for name,want in m['raw_and_result_hashes'].items():assert digest(R/name)==want,name
    checks['run_input_and_result_hashes']=len(m['raw_and_result_hashes'])
    db=R.parent/'moomoo_research_dataset_20261119/dataset_v0_1/market_research.sqlite'
    assert digest(db)==m['price_database_sha256']
    panels={};count=0;features=0;outcomes=0;alignments=0
    sources={}
    for sid in ['VIXCLS','BAA10Y','DGS10']:
        a=pd.read_csv(R/'raw'/f'{sid}.csv',parse_dates=[0]).set_index('observation_date').apply(pd.to_numeric,errors='coerce').dropna();sources[sid]=a
    for sym,autype in impl.C['symbols_autype'].items():
        a=pd.read_csv(P/(sym.replace('.','_')+'_panel.csv'),parse_dates=['date']).set_index('date');panels[sym]=a
        with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as con:
            raw=pd.read_sql_query('SELECT local_date,close FROM research_daily WHERE symbol=? AND autype=? ORDER BY local_date',con,params=[sym,autype],parse_dates=['local_date']).set_index('local_date').loc['1996-01-01':'2026-11-17']
        assert raw.index.equals(a.index);np.testing.assert_allclose(raw.close,a.close,rtol=1e-14,atol=1e-12);count+=len(a)
        for _,b in a.groupby('segment'):
            values=b.close.to_numpy()
            for j in range(len(b)):
                for n in [20,60]:
                    v=values[j]/values[j-n]-1 if j>=n else np.nan
                    np.testing.assert_allclose(v,b[f'return{n}'].iloc[j],atol=1e-12,equal_nan=True);features+=1
                ma=values[j]/np.mean(values[j-59:j+1])-1 if j>=59 else np.nan
                dd=values[j]/max(values[j-251:j+1])-1 if j>=251 else np.nan
                np.testing.assert_allclose([ma,dd],b[['ma60_gap','drawdown252']].iloc[j].to_numpy(float),atol=1e-12,equal_nan=True);features+=2
                vol=np.std(np.diff(values[j-20:j+1])/values[j-20:j],ddof=1)*np.sqrt(252) if j>=20 else np.nan
                np.testing.assert_allclose(vol,b.volatility20.iloc[j],atol=1e-11,equal_nan=True);features+=1
        for sid,source in sources.items():
            actual=pd.to_datetime(a[sid+'_date'])
            jj=source.index.searchsorted(a.index,side='left')-2
            for j,d in enumerate(a.index):
                k=jj[j];expect=source.index[k] if k>=0 and (d-source.index[k]).days<=7 else pd.NaT
                assert (pd.isna(actual.iloc[j]) and pd.isna(expect)) or actual.iloc[j]==expect,(sym,sid,d)
                alignments+=1
        for h in [4,8,12]:
            for j in range(len(a)):
                v=a[f'future{h}'].iloc[j]
                if pd.isna(v):continue
                entry=a.index[j+1];target=entry+pd.Timedelta(weeks=h)
                end=a.index[a.index<=target][-1];end_j=a.index.get_loc(end)
                assert target<=a.index[-1] and (target-end).days<=7
                assert a.segment.iloc[j]==a.segment.iloc[end_j]
                assert impl.period(end)==impl.period(a.index[j])==impl.period(entry)
                np.testing.assert_allclose(v,raw.close.loc[end]/raw.close.loc[entry]-1,atol=1e-12);outcomes+=1
    checks.update(price_rows_from_original_sqlite=count,direct_price_feature_cells=features,source_date_alignments=alignments,forward_returns_from_original_sqlite=outcomes)
    # Direct rank check on arbitrary fixed positions, including ties; no full-sample normalization.
    v=sources['VIXCLS'].iloc[:,0]
    for i in range(800,len(v),251):
        w=v.iloc[max(0,i-1259):i+1].to_numpy();rank=(np.count_nonzero(w<v.iloc[i])+(np.count_nonzero(w==v.iloc[i])+1)/2)/len(w)
        expected=100*(1-rank)
        np.testing.assert_allclose(expected,impl.source_features()['VIXCLS'].risk_appetite.iloc[i],atol=1e-12)
    checks['trailing_vix_rank_direct_checks']=len(range(800,len(v),251))
    ev=pd.read_csv(O/'events_all_thresholds.csv');ec=0;annotations=0
    for _,e in ev.iterrows():
        a=panels[e.symbol];b=a.loc[a.segment.eq(e.segment)];q=e.threshold;up=e.direction=='UP'
        origin=pd.Timestamp(e.origin_extreme_date_hindsight);confirm=pd.Timestamp(e.threshold_confirmation_date);end=pd.Timestamp(e.end_extreme_date_hindsight)
        r=b.close.loc[confirm]/b.close.loc[origin]-1
        assert r>=q-1e-12 if up else r<=-q+1e-12
        early=b.close.loc[(b.index>origin)&(b.index<confirm)]/b.close.loc[origin]-1
        assert not ((early>=q) if up else (early<=-q)).any()
        limit=pd.Timestamp(e.next_reversal_confirmation_date) if pd.notna(e.next_reversal_confirmation_date) else b.index[-1]
        future=b.close.loc[confirm:limit]
        expected_end=future.idxmax() if up else future.idxmin()
        assert end==expected_end,(e.event_id,end,expected_end)
        if bool(e.complete):
            target=b.close.loc[limit]/b.close.loc[end]-1
            assert target<=-q+1e-12 if up else target>=q-1e-12
        for rule in impl.RULES:
            for side in ['pre','post']:
                value=e[f'{rule}_{side}20_onsets']
                if pd.isna(value):continue
                for d in str(value).split('|'):
                    assert a.loc[pd.Timestamp(d),'segment']==e.segment;annotations+=1
        ec+=1
    checks.update(event_threshold_first_crossing_and_extreme_checks=ec,event_annotation_same_segment_entries=annotations)
    alerts=pd.read_csv(P/'alert_outcomes.csv');unique=alerts[['symbol','rule','signal_date']].drop_duplicates()
    for _,x in unique.iterrows():
        a=panels[x.symbol];j=a.index.get_loc(pd.Timestamp(x.signal_date));cols=['return20','ma60_gap'] if x.rule.startswith('price') else ['VIXCLS_date'] if x.rule.startswith('risk') else ['return20','ma60_gap','VIXCLS_date']
        assert j>0 and a.segment.iloc[j]==a.segment.iloc[j-1]
        assert a.iloc[j-1][cols].notna().all() and a.iloc[j][cols].notna().all()
        assert not bool(a[x.rule].iloc[j-1]) and bool(a[x.rule].iloc[j])
    for (sym,rule),b in unique.groupby(['symbol','rule']):
        a=panels[sym];ds=pd.to_datetime(b.signal_date);prev=None
        for d in ds:
            j=a.index.get_loc(d)
            if prev is not None and a.segment.iloc[prev]==a.segment.iloc[j]:assert j-prev>=20
            prev=j
    checks['alerts_known_false_to_true_and_cooldown']=len(unique)
    for _,x in alerts.iterrows():
        if x.status=='CENSORED':continue
        a=panels[x.symbol];j=a.index.get_loc(pd.Timestamp(x.signal_date))+1;k=a.index.get_loc(pd.Timestamp(a.iloc[j-1][f'end{x.horizon_weeks}']))
        rr=a.close.iloc[j:k+1].to_numpy()/a.close.iloc[j]-1;status='NO_BARRIER'
        for z,value in enumerate(rr):
            if value*x.direction>=x.barrier:status='DESIRED_FIRST';break
            if value*x.direction<=-x.barrier:status='OPPOSITE_FIRST';break
        assert x.status==status
    checks['alert_barrier_cells']=len(alerts)
    # Future mutation and prefix invariance of prospective price/lag alignment,
    # plus independent augmented-system least-squares refit for every saved model.
    fits=json.loads((P/'model_fit_parameters.json').read_text());pred=pd.read_csv(P/'model_predictions.csv',parse_dates=['date'])
    for f in fits:
        a=panels[f['symbol']];h=f['horizon_weeks'];cut=pd.Timestamp(f['year'],1,1)
        valid=a.dropna(subset=impl.FEATURES+impl.VF+impl.FF+[f'future{h}']).copy()
        valid[f'end{h}']=pd.to_datetime(valid[f'end{h}'])
        train=valid.loc[(valid.index<cut)&(valid[f'end{h}']<cut)]
        assert len(train)==f['train_rows']>=756 and train[f'end{h}'].max()<cut
        x=train[f['features']].to_numpy();y=train[f'future{h}'].to_numpy()
        mu=x.mean(0);sd=x.std(0);sd=np.where(sd>1e-12,sd,1.)
        z=(x-mu)/sd
        beta=np.linalg.lstsq(np.vstack([z,np.eye(z.shape[1])]),np.r_[y-y.mean(),np.zeros(z.shape[1])],rcond=None)[0]
        np.testing.assert_allclose(beta,f['params']['beta'],atol=2e-10)
        q=pred.loc[pred.symbol.eq(f['symbol'])&pred.horizon_weeks.eq(h)&pred.year.eq(f['year'])]
        xt=a.loc[pd.DatetimeIndex(q.date),f['features']].to_numpy()
        calc=y.mean()+(xt-mu)/sd@beta
        np.testing.assert_allclose(calc,q[f['model']],atol=2e-10)
    checks['models_train_only_and_augmented_system_refit']=len(fits)
    for sym,a in panels.items():
        # Outcomes are not inputs; perturb future raw prices before recomputing
        # historical price features directly.
        cut=len(a)//2;p=a.close.copy();p.iloc[cut+1:]*=1.7
        for n in [20,60]:
            np.testing.assert_allclose((p/p.shift(n)-1).iloc[:cut+1],(a.close/a.close.shift(n)-1).iloc[:cut+1],atol=1e-12,equal_nan=True)
        short=impl.align_sources(a.index[:cut+1],impl.source_features());long=impl.align_sources(a.index,impl.source_features())
        pd.testing.assert_frame_equal(short,long.iloc[:cut+1])
    checks['future_price_mutation_and_alignment_prefix_markets']=len(panels)
    v1=pd.read_csv(R/'results/model_contrasts.csv');v11=pd.read_csv(O/'model_contrasts.csv')
    pd.testing.assert_frame_equal(v1,v11)
    pd.testing.assert_frame_equal(pd.read_csv(R/'results/associations.csv'),pd.read_csv(O/'associations.csv'))
    checks['implementation_fix_did_not_change_models_or_associations']=True
    result={'status':'PASS_SAME_AUTHOR_SELF_CHECK','independent_validation':False,'checks':checks,'known_v1_failures':'AMENDMENT_v1_1.json','scientific_limits':['current-vintage inputs, no historical information-set certification','event extrema are retrospective','sparse independent episodes and correlated market proxies','China local investor sentiment/news expectation data unavailable'],'code_sha256':digest(__file__),'checked_run_manifest_sha256':digest(O/'run_manifest.json'),'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (O/'self_check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)

if __name__=='__main__':main()
