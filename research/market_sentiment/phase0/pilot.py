"""Descriptive, current-vintage monthly screen. NOT an executable strategy backtest."""
from pathlib import Path
import json, hashlib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
CONFIG = {
    'run_id': 'sentiment_pilot_20261119_v1',
    'seed': 20261119, 'bootstrap_reps': 2000, 'block_months': 12,
    'panic_vix_level': 30, 'recent_panic_months': 3, 'momentum_months': 3,
    'outcome': 'P[t+4]/P[t+1]-1', 'outcome_end': '2025-12',
    'US_start': '1996-01', 'CN_start': '1999-01',
    'later_segment_start': '2016-01',
    'status': 'EXPLORATORY_CURRENT_VINTAGE_DESCRIPTIVE_NOT_TRADABLE_RETURNS',
}
(ROOT/'pilot_config.json').write_text(json.dumps(CONFIG, indent=2))
def read(sid):
    d = pd.read_csv(ROOT/'raw'/f'{sid}.csv', index_col=0, parse_dates=True)
    s = pd.to_numeric(d.iloc[:, 0], errors='coerce').dropna()
    assert s.index.is_unique and s.index.is_monotonic_increasing
    return s

vix = read('VIXCLS').groupby(lambda x: x.to_period('M')).last()
rng = np.random.default_rng(CONFIG['seed'])
summaries=[]
panels=[]
for market, sid in [('US','SPASTT01USM661N'), ('CN','SPASTT01CNM661N')]:
    p = read(sid)
    p.index = p.index.to_period('M')
    p = p.reindex(pd.period_range(p.index.min(), '2025-12', freq='M'))
    d = pd.DataFrame({'price_proxy':p, 'vix_eom':vix.reindex(p.index)})
    d['momentum3'] = d.price_proxy / d.price_proxy.shift(3)-1
    d['outcome3_gap1'] = d.price_proxy.shift(-4)/d.price_proxy.shift(-1)-1
    d['panic_level'] = d.vix_eom >= CONFIG['panic_vix_level']
    d['panic_easing'] = (d.vix_eom.rolling(3).max() >= 30) & (d.vix_eom < d.vix_eom.shift(1))
    d['momentum_only'] = d.momentum3 > 0
    d['easing_plus_momentum'] = d.panic_easing & d.momentum_only
    d['all_months'] = True
    d = d.loc[CONFIG[market+'_start']:].dropna(subset=['price_proxy','vix_eom','momentum3','outcome3_gap1'])
    d['market'] = market
    d.index.name='signal_month'
    panels.append(d)
    for segment, x in [('full',d), ('2016_2025',d.loc['2016-01':])]:
        y=x.outcome3_gap1.to_numpy()
        n=len(y)
        # Same resampled calendar blocks are used across every rule in this segment.
        starts = rng.integers(0,n,size=(CONFIG['bootstrap_reps'],int(np.ceil(n/12))))
        idx=((starts[:,:,None]+np.arange(12)[None,None,:]) % n).reshape(CONFIG['bootstrap_reps'],-1)[:,:n]
        yb=y[idx]
        for rule in ['all_months','panic_level','panic_easing','easing_plus_momentum','momentum_only']:
            mask=x[rule].to_numpy(dtype=bool)
            ys=y[mask]
            mb=mask[idx]
            den=mb.sum(axis=1)
            good=den>0
            effects=((yb*mb).sum(axis=1)[good]/den[good])-yb.mean(axis=1)[good]
            ci=np.quantile(effects,[.025,.975]).tolist() if len(effects) else [None,None]
            count=int(mask.sum())
            episodes=int(np.sum(mask & ~np.r_[False,mask[:-1]]))
            summaries.append({
                'market':market,'segment':segment,'rule':rule,
                'first_signal_month':str(x.index.min()),'last_signal_month':str(x.index.max()),
                'eligible_months':n,'signal_months':count,'contiguous_signal_episodes':episodes,
                'mean_index_change':float(ys.mean()) if count else None,
                'median_index_change':float(np.median(ys)) if count else None,
                'positive_fraction':float((ys>0).mean()) if count else None,
                'effect_vs_all_months':float(ys.mean()-y.mean()) if count else None,
                'effect_ci95_low':ci[0], 'effect_ci95_high':ci[1],
                'bootstrap_valid_reps':int(good.sum()),
                'signal_dates':[str(t) for t in x.index[mask]],
            })

panel=pd.concat(panels)
panel.to_csv(ROOT/'pilot_panel.csv')
(ROOT/'pilot_results.json').write_text(json.dumps(summaries,indent=2))
pd.DataFrame([{k:v for k,v in r.items() if k!='signal_dates'} for r in summaries]).to_csv(ROOT/'pilot_summary.csv',index=False)
manifest=json.loads((ROOT/'raw_manifest.json').read_text())
run={
    'config':CONFIG,
    'config_sha256':hashlib.sha256((ROOT/'pilot_config.json').read_bytes()).hexdigest(),
    'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'input_hashes':{x['series']:x['sha256'] for x in manifest if x.get('series')!='BAMLH0A0HYM2' and 'sha256' in x},
    'output_hashes':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['pilot_results.json','pilot_panel.csv','pilot_summary.csv']},
    'numpy':np.__version__,'pandas':pd.__version__,
    'excluded':{'BAMLH0A0HYM2':'Only three-year public history; not tested.', '2026':'Excluded from all computed outcomes.'},
}
(ROOT/'pilot_run_manifest.json').write_text(json.dumps(run,indent=2))
print(pd.read_csv(ROOT/'pilot_summary.csv').round(5).to_string(index=False))
