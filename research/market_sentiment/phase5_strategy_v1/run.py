"""Fixed OHLC research simulation. Never connects to brokerage or submits orders."""
from pathlib import Path
import argparse,datetime,gzip,hashlib,json,math,platform,sqlite3
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT=Path(__file__).resolve().parent
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def finite(x): return float(x) if np.isfinite(x) else None
def tag(symbol): return symbol.replace('.','_')

def features(d,cfg):
    d=d.copy().reset_index(drop=True);c=d['close'];t=cfg['trend'];r=cfg['recovery']
    lr=np.log(c/c.shift(1));d['vol20']=lr.rolling(t['vol_bars'],min_periods=t['vol_bars']).std(ddof=0)
    for n in [5,20,60]:d['r'+str(n)]=c/c.shift(n)-1
    for n in [20,60,120,200]:d['ma'+str(n)]=c.rolling(n,min_periods=n).mean()
    d['dd252']=c/c.rolling(r['drawdown_bars'],min_periods=r['drawdown_bars']).max()-1
    denom=d['vol20'].where(d['vol20']>0)
    d['trend_score']=np.log(c/c.shift(t['momentum_bars']))/(denom*np.sqrt(t['momentum_bars']))
    stress=(d['dd252']<=r['drawdown_threshold'])&(d['r20']<=r['stress_return_threshold'])
    d['recent_stress']=stress.astype(float).rolling(r['stress_memory_bars'],min_periods=1).max()>0
    d['recovery_score']=np.where(d['recent_stress'],d['r5']/(denom*np.sqrt(r['confirmation_return_bars'])),0.)
    d['inverse_vol_budget']=(cfg['risk_budget']['annual_target_vol']/(denom*np.sqrt(cfg['risk_budget']['annual_bars']))).clip(upper=cfg['risk_budget']['max_exposure'])
    p1=[];p2=[];s1=False;s2=False;entered=-1;peak=0.
    for i,row in d.iterrows():
        if i<cfg['warmup_closes']-1:
            p1.append(0.);p2.append(0.);continue
        if s1:
            if row['trend_score']<=t['exit_score'] or row['close']<row['ma'+str(t['exit_ma'])]:s1=False
        elif row['trend_score']>t['entry_score'] and row['close']>row['ma'+str(t['entry_ma'])]:s1=True
        if s2:
            peak=max(peak,row['close'])
            if i-entered>=r['maximum_hold_bars'] or row['close']/peak-1<=-r['close_trailing_stop']:s2=False
        elif row['recent_stress'] and row['r5']>0 and row['close']>row['ma'+str(r['confirmation_ma'])]:
            s2=True;entered=i;peak=row['close']
        p1.append(float(s1));p2.append(float(s2))
    d['BUY_HOLD']=1.;d['MA200']=(c>d['ma200']).astype(float)
    d['P1_TREND']=p1;d['P2_RECOVERY']=p2
    d['P3_RISK_BUDGET']=d['P1_TREND']*d['inverse_vol_budget'].fillna(0.)
    return d

def load_data(db,cfg,end):
    conn=sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro',uri=True)
    out={};inventory=[]
    for symbol,autype in cfg['series'].items():
        d=pd.read_sql_query('select local_date,open,close from research_daily where symbol=? and autype=? and local_date<=? order by local_date',conn,params=[symbol,autype,end])
        assert len(d)>0 and not d['local_date'].duplicated().any()
        assert np.isfinite(d[['open','close']]).all().all() and (d[['open','close']]>0).all().all()
        missing=[x[0] for x in conn.execute("select local_date from unresolved_dates where symbol=? and frequency='DAY' order by local_date",[symbol])]
        dates=d['local_date'].to_numpy();cuts={0,len(d)}
        for day in missing:
            j=int(np.searchsorted(dates,day));
            if 0<j<len(d):cuts.add(j)
        cuts=sorted(cuts);parts=[]
        for ep,(a,b) in enumerate(zip(cuts[:-1],cuts[1:])):
            part=features(d.iloc[a:b],cfg);part['episode']=ep;parts.append(part)
        out[symbol]=parts
        inventory.append({'symbol':symbol,'autype':autype,'rows':len(d),'first':str(dates[0]),'last':str(dates[-1]),'episodes':[{'id':int(x['episode'].iloc[0]),'n':len(x),'start':x['local_date'].iloc[0],'end':x['local_date'].iloc[-1]} for x in parts],'listed_missing_dates':missing})
    conn.close();return out,inventory

def rebalance(cash,units,price,w,cost):
    nav=cash+units*price;old=units*price
    y=w*(nav+cost*old)/(1+w*cost) if w*nav>=old else w*(nav-cost*old)/(1-w*cost)
    traded=y-old;fee=cost*abs(traded);new_cash=cash-traded-fee;new_units=y/price
    assert new_cash>-1e-10 and new_units>=0 and 0<=w<=1
    return max(new_cash,0.),new_units,traded,fee

def simulate(parts,strategy,cost_bps,start,end,cfg):
    cash=1.;units=0.;rows=[];c=cost_bps/10000
    for d in parts:
        # Only actual accepted opens inside the evaluation period; no boundary label crosses.
        ix=[j for j in range(cfg['warmup_closes'],len(d)) if start<=d.at[j,'local_date']<=end]
        if len(ix)<2:continue
        for k,j in enumerate(ix[:-1]):
            nxt=ix[k+1];assert nxt==j+1
            price=float(d.at[j,'open']);mark=float(d.at[nxt,'open']);w=float(d.at[j-1,strategy])
            before_cash,before_units=cash,units;nav=cash+units*price
            cash,units,traded,fee=rebalance(cash,units,price,w,c)
            post=cash+units*price;after_cash,after_units=cash,units
            terminal=k==len(ix)-2
            terminal_value=units*mark if terminal else 0.;exit_fee=c*terminal_value
            end_nav=cash+units*mark-exit_fee
            if terminal:cash=end_nav;units=0.
            rows.append({'signal_date':d.at[j-1,'local_date'],'exec_date':d.at[j,'local_date'],'mark_date':d.at[nxt,'local_date'],'episode':int(d.at[j,'episode']),
                'open_exec':price,'open_mark':mark,'target_weight':w,'nav_before':nav,'cash_before':before_cash,'units_before':before_units,
                'trade_value':traded,'fee_entry':fee,'cash_after':after_cash,'units_after':after_units,'nav_posttrade':post,
                'terminal_liquidation':terminal,'fee_terminal':exit_fee,'end_nav':end_nav,'return_net':end_nav/nav-1,
                'turnover_fraction':(abs(traded)+terminal_value)/nav,'cost_fraction':(fee+exit_fee)/nav,
                'weight_during':after_units*price/post,'cash_end':cash,'units_end':units,'price_return':mark/price-1})
    return pd.DataFrame(rows)

def metrics(l):
    if l.empty:return {'status':'NO_ELIGIBLE_INTERVALS'}
    ret=l['return_net'].to_numpy();nav=np.r_[1.,l['end_nav'].to_numpy()]
    years=(pd.Timestamp(l.iloc[-1]['mark_date'])-pd.Timestamp(l.iloc[0]['exec_date'])).days/365.2425
    vol=np.std(ret,ddof=0);entered=(l['units_before']<1e-14)&(l['units_after']>1e-14)
    return {'status':'EXECUTED_PRICE_ONLY','first_exec':l.iloc[0]['exec_date'],'last_mark':l.iloc[-1]['mark_date'],'intervals':len(l),
        'terminal_wealth':nav[-1],'total_return':nav[-1]-1,'cagr':nav[-1]**(1/years)-1 if years>0 else np.nan,
        'max_drawdown':float(np.min(nav/np.maximum.accumulate(nav)-1)),'daily_mean_net':np.mean(ret),
        'descriptive_sharpe_sqrt252':np.mean(ret)/vol*np.sqrt(252) if vol>0 else np.nan,
        'mean_exposure':l['weight_during'].mean(),'annualized_turnover':l['turnover_fraction'].sum()/years if years>0 else np.nan,
        'cost_fraction_sum':l['cost_fraction'].sum(),'entry_episodes':int(entered.sum()),'boundary_exits':int(l['terminal_liquidation'].sum())}

def circular_means(x,block,replicates,seed):
    x=np.asarray(x,dtype=float);n=len(x);block=min(block,n);k=math.ceil(n/block)
    rng=np.random.default_rng(seed);starts=rng.integers(0,n,size=(replicates,k))
    doubled=np.r_[x,x];cs=np.r_[0.,np.cumsum(doubled)]
    lengths=np.full(k,block);lengths[-1]=n-block*(k-1)
    return np.sum(cs[starts+lengths]-cs[starts],axis=1)/n

def holm(p):
    p=np.asarray(p);order=np.argsort(p);adj=np.empty(len(p));prev=0.
    for rank,j in enumerate(order):prev=max(prev,(len(p)-rank)*p[j]);adj[j]=min(1.,prev)
    return adj

def diagnostics(parts,start,end,cfg):
    rows=[];h=cfg['factor_diagnostics']['forward_calendar_weeks']*7
    for field in cfg['factor_diagnostics']['fields']:
        xs=[];ys=[]
        for d in parts:
            dates=d['local_date'].to_numpy()
            for j in range(cfg['warmup_closes'],len(d)):
                if not start<=dates[j]<=end:continue
                target=(pd.Timestamp(dates[j])+pd.Timedelta(days=h)).strftime('%Y-%m-%d');k=int(np.searchsorted(dates,target))
                if k>=len(d) or dates[k]>end:continue
                x=d.at[j-1,field]
                if not np.isfinite(x):continue
                xs.append(float(x));ys.append(float(d.at[k,'open']/d.at[j,'open']-1))
        rho=float(spearmanr(xs,ys).statistic) if len(xs)>2 and len(set(xs))>1 else np.nan
        rows.append({'factor':field,'n_labels':len(xs),'distinct_factor_values':len(set(xs)),'spearman_8w':rho,'iid_p_value':'NOT_REPORTED_DEPENDENT_LABELS'})
    return rows

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--private-out',required=True);p.add_argument('--public-out',required=True)
    p.add_argument('--freeze-commit',required=True);p.add_argument('--mode',choices=['exact','external'],default='exact');p.add_argument('--end')
    a=p.parse_args();cfg=json.loads((ROOT/'config.json').read_text());private=Path(a.private_out);public=Path(a.public_out)
    assert not private.exists() and not public.exists(),'Refuse to overwrite any previous run; use new output directories'
    assert len(a.freeze_commit)==40 and all(c in '0123456789abcdef' for c in a.freeze_commit)
    db_sha=sha(a.db)
    if a.mode=='exact':assert db_sha==cfg['expected_db_sha256'],'Exact reproduction requires identical DB'
    end=a.end or cfg['data_end'];assert a.mode=='external' or end==cfg['data_end']
    started=datetime.datetime.now(datetime.timezone.utc).isoformat();private.mkdir(parents=True);public.mkdir(parents=True)
    data,inventory=load_data(a.db,cfg,end);write_json(public/'input_inventory.json',inventory)
    summaries=[];contrasts=[];factors=[];hashes={};primary_ledgers={}
    periods={k:list(v) for k,v in cfg['periods'].items()}
    for k in ['full','monitor']:periods[k][1]=end
    for symbol,parts in data.items():
        for period,(start,stop) in periods.items():
            stop=min(stop,end)
            for x in diagnostics(parts,start,stop,cfg):factors.append({'symbol':symbol,'period':period,**x})
            for cost in cfg['cost_bps_one_way']:
                for strategy in cfg['strategies']:
                    ledger=simulate(parts,strategy,cost,start,stop,cfg)
                    row={'symbol':symbol,'period':period,'cost_bps':cost,'strategy':strategy,**metrics(ledger)}
                    summaries.append(row)
                    if ledger.empty:continue
                    rel=f'ledgers/{tag(symbol)}/{period}/{strategy}_c{cost}.csv.gz';f=private/rel;f.parent.mkdir(parents=True,exist_ok=True)
                    raw=ledger.to_csv(index=False,float_format='%.17g',lineterminator='\n').encode()
                    f.write_bytes(gzip.compress(raw,mtime=0));hashes[rel]={'uncompressed_sha256':hashlib.sha256(raw).hexdigest(),'compressed_sha256':sha(f),'rows':len(ledger)}
                    if period=='primary':primary_ledgers[(symbol,cost,strategy)]=ledger
        for strategy in cfg['strategies'][2:]:
            key=(symbol,cfg['primary_cost_bps'],strategy);base_key=(symbol,cfg['primary_cost_bps'],'MA200')
            if key not in primary_ledgers or base_key not in primary_ledgers:continue
            s=primary_ledgers[key];b=primary_ledgers[base_key]
            assert s[['exec_date','mark_date']].equals(b[['exec_date','mark_date']])
            delta=s['return_net'].to_numpy()-b['return_net'].to_numpy();st=cfg['statistics']
            for block,seed in [(st['primary_block'],st['seed'])]+[(x,st['seed']) for x in st['block_sensitivity']]+[(st['primary_block'],st['secondary_seed'])]:
                boot=circular_means(delta,block,st['replicates'],seed);mean=float(delta.mean());lo,hi=np.quantile(boot,[.025,.975])
                # Bootstrap under centered null, predeclared positive alternative.
                pval=(1+int(np.sum(boot-mean>=mean)))/(st['replicates']+1)
                highcost=primary_ledgers[(symbol,25,strategy)]['return_net'].to_numpy()-primary_ledgers[(symbol,25,'MA200')]['return_net'].to_numpy()
                contrasts.append({'symbol':symbol,'strategy':strategy,'period':'primary','cost_bps':cfg['primary_cost_bps'],'n':len(delta),'block':block,'seed':seed,
                    'primary_test':block==st['primary_block'] and seed==st['seed'],'mean_daily_net_delta':mean,'annualized_arithmetic_delta':mean*252,
                    'ci_low_daily':float(lo),'ci_high_daily':float(hi),'p_one_sided_positive':pval,'mean_daily_delta_cost25':float(highcost.mean())})
    primary=[x for x in contrasts if x['primary_test']]
    assert len(primary)==12 if a.mode=='exact' else True
    for row,adj in zip(primary,holm([x['p_one_sided_positive'] for x in primary])):
        row['holm_p']=float(adj);row['first_screen_return_increment_pass']=bool(row['mean_daily_net_delta']>0 and row['ci_low_daily']>0 and adj<=.05 and row['mean_daily_delta_cost25']>0)
    for name,rows in [('metrics',summaries),('contrasts',contrasts),('factor_diagnostics',factors)]:
        pd.DataFrame(rows).to_csv(public/(name+'.csv'),index=False,float_format='%.17g')
    write_json(public/'private_output_hashes.json',hashes)
    write_json(public/'text_candidates.json',{'T1':'BLOCKED_NO_NEWS_PANEL','T2':'BLOCKED_NO_TIME_STAMPED_NEGATIVE_EVENTS_AND_EXPECTED_RESPONSE_MODEL','price_candidates_are_not_text_sentiment':True})
    manifest={'study_id':cfg['study_id'],'mode':a.mode,'started_at_utc':started,'finished_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'freeze_commit':a.freeze_commit,'input_db_sha256':db_sha,'config_sha256':sha(ROOT/'config.json'),'code_sha256':sha(__file__),
        'protocol_sha256':sha(ROOT/'PROTOCOL_zh.md'),'data_end':end,'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,
        'formula_or_threshold_tuning':False,'input_vintage':'CURRENT_NOT_PIT_CERTIFIED','history_previously_seen':True,'independent_validation':False,
        'real_broker_orders':0,'fitted_financial_models':0,'simulation_summary_rows':len(summaries),'private_ledger_files':len(hashes),
        'primary_family_size':len(primary),'primary_pass_count':sum(x['first_screen_return_increment_pass'] for x in primary),
        'outputs_sha256':{f.name:sha(f) for f in sorted(public.iterdir()) if f.is_file()},'limitations':cfg['known_limits']}
    write_json(public/'run_manifest.json',manifest)
    print(json.dumps({'status':'EXECUTED_EXPLORATORY_PRICE_SIMULATION','summary_rows':len(summaries),'ledger_files':len(hashes),'primary_tests':len(primary),'first_screen_passes':manifest['primary_pass_count'],'news_factors_run':False}))

if __name__=='__main__':main()
