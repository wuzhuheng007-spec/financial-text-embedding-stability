"""Same-author checks: causal features and a separate accounting reconciliation."""
from pathlib import Path
import argparse,copy,gzip,hashlib,json,datetime
import numpy as np
import pandas as pd
import run

ROOT=Path(__file__).resolve().parent
def units(cfg):
    n=620;close=100*np.exp(.0004*np.arange(n)+.08*np.sin(np.arange(n)/27))
    d=pd.DataFrame({'local_date':pd.bdate_range('2001-01-01',periods=n).strftime('%Y-%m-%d'),'open':close*.999,'close':close})
    full=run.features(d,cfg);fields=cfg['strategies']+cfg['factor_diagnostics']['fields']
    for end in [280,400,519]:
        cut=run.features(d.iloc[:end],cfg)
        assert np.allclose(cut[fields],full.iloc[:end][fields],equal_nan=True)
    mutated=d.copy();mutated.loc[450:,'close']*=3
    assert np.allclose(run.features(mutated,cfg).iloc[:450][fields],full.iloc[:450][fields],equal_nan=True)
    sample=full.iloc[:255].copy();sample['open']=100.;sample.loc[253,'open']=110.;sample.loc[254,'open']=120.;sample['episode']=0
    for bps in [0,10,25]:
        ledger=run.simulate([sample],'BUY_HOLD',bps,'2000-01-01','2010-01-01',cfg)
        c=bps/10000;expected=1.2*(1-c)/(1+c)
        assert np.isclose(ledger.iloc[-1]['end_nav'],expected,atol=1e-12)
        assert all(ledger['signal_date']<ledger['exec_date']) and all(ledger['exec_date']<ledger['mark_date'])
    # Costed fractional allocation reconciles target against post-fee wealth.
    for cash,u,w in [(1.,0.,1.),(.2,.008,.3),(.8,.002,.7),(1.,0.,0.)]:
        cash2,u2,traded,fee=run.rebalance(cash,u,100.,w,.001)
        assert np.isclose(fee,abs((u2-u)*100)*.001)
        assert np.isclose(cash2+u2*100,cash+u*100-fee)
        assert np.isclose(u2*100/(cash2+u2*100),w)
    return {'status':'PASS_SYNTHETIC_CAUSALITY_AND_ACCOUNTING_ONLY','prefix_lengths':[280,400,519],'future_perturbation_cut':450,'analytical_cost_cases_bps':[0,10,25],'allocation_cases':4}

def check_results(public,private,cfg,db):
    manifest=json.loads((public/'run_manifest.json').read_text())
    assert manifest['config_sha256']==run.sha(ROOT/'config.json') and manifest['code_sha256']==run.sha(ROOT/'run.py')
    assert run.sha(db)==manifest['input_db_sha256']
    for f,h in manifest['outputs_sha256'].items():assert run.sha(public/f)==h,f
    hashes=json.loads((public/'private_output_hashes.json').read_text());summary=pd.read_csv(public/'metrics.csv');count=0
    for rel,identity in hashes.items():
        raw=gzip.decompress((private/rel).read_bytes())
        assert hashlib.sha256(raw).hexdigest()==identity['uncompressed_sha256'],rel
        l=pd.read_csv(private/rel);assert len(l)==identity['rows']
        symbol_tag,period,filename=rel.split('/')[1:];strategy,coststring=filename.rsplit('_c',1);bps=int(coststring.split('.')[0]);c=bps/10000
        assert (l['signal_date']<l['exec_date']).all() and (l['exec_date']<l['mark_date']).all()
        # Reconstruct cash, units and fees from fills rather than simulator summaries.
        nav=l.cash_before+l.units_before*l.open_exec
        dv=(l.units_after-l.units_before)*l.open_exec
        fee=np.abs(dv)*c
        assert np.allclose(l.nav_before,nav,atol=1e-10)
        assert np.allclose(l.trade_value,dv,atol=1e-10)
        assert np.allclose(l.fee_entry,fee,atol=1e-10)
        assert np.allclose(l.cash_after,l.cash_before-dv-fee,atol=1e-10)
        assert (l.cash_after>=-1e-10).all()
        terminal=l.terminal_liquidation.astype(bool)
        exit_fee=np.where(terminal,l.units_after*l.open_mark*c,0.)
        end=l.cash_after+l.units_after*l.open_mark-exit_fee
        assert np.allclose(l.fee_terminal,exit_fee,atol=1e-10)
        assert np.allclose(l.end_nav,end,atol=1e-10)
        assert np.allclose(l.return_net,end/nav-1,atol=1e-11)
        assert np.allclose(l.target_weight,l.units_after*l.open_exec/(nav-fee),atol=1e-10)
        assert np.allclose(l.nav_before.iloc[1:],l.end_nav.iloc[:-1],atol=1e-10)
        assert np.isclose(l.nav_before.iloc[0],1.)
        eq=np.r_[1.,end.to_numpy()];dd=np.min(eq/np.maximum.accumulate(eq)-1)
        symbol=next(s for s in cfg['series'] if run.tag(s)==symbol_tag)
        row=summary[(summary.symbol==symbol)&(summary.period==period)&(summary.cost_bps==bps)&(summary.strategy==strategy)].iloc[0]
        years=(pd.Timestamp(l.mark_date.iloc[-1])-pd.Timestamp(l.exec_date.iloc[0])).days/365.2425
        assert np.isclose(row.terminal_wealth,eq[-1]) and np.isclose(row.max_drawdown,dd)
        assert np.isclose(row.cagr,eq[-1]**(1/years)-1)
        count+=1
    co=pd.read_csv(public/'contrasts.csv');primary=co[co.primary_test.astype(bool)]
    expected=run.holm(primary.p_one_sided_positive.to_numpy());assert np.allclose(primary.holm_p,expected)
    assert len(primary)==manifest['primary_family_size']
    # Real-input prefixes must yield unchanged past signals. No future labels used here.
    data,_=run.load_data(db,cfg,manifest['data_end']);prefixes=0
    for parts in data.values():
        for d in parts:
            if len(d)<300:continue
            k=min(len(d)-1,max(280,len(d)//2));short=run.features(d.iloc[:k][['local_date','open','close']],cfg)
            fields=cfg['strategies']+cfg['factor_diagnostics']['fields']
            assert np.allclose(short[fields],d.iloc[:k][fields],equal_nan=True);prefixes+=1
    return {'status':'PASS_SAME_AUTHOR_LEDGER_AND_INPUT_CHECK','ledger_files_recomputed':count,'real_prefix_cases':prefixes,'primary_tests':len(primary),'independent_external_validation':False,'news_factors_validated':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--unit',action='store_true');p.add_argument('--public-out');p.add_argument('--private-out');p.add_argument('--db');a=p.parse_args()
    cfg=json.loads((ROOT/'config.json').read_text());result={'synthetic':units(cfg)}
    if not a.unit:
        assert a.public_out and a.private_out and a.db
        result['real_run']=check_results(Path(a.public_out),Path(a.private_out),cfg,a.db)
        result['checked_at_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        run.write_json(Path(a.public_out)/'self_check.json',result)
    print(json.dumps(result))
