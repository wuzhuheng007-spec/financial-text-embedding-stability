"""Reconcile public aggregates with saved raw prediction/alert evidence."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

R=Path(__file__).resolve().parent;O=R/'results_v1_1';P=R/'private_v1_1'
def main():
    display=json.loads((O/'display_cells.json').read_text());checks={}
    events=pd.read_csv(O/'events_all_thresholds.csv');primary=events.loc[events.threshold.eq(.2)]
    assert len(primary)==sum(x['n'] for x in display['event_counts'])
    atlas=(R/'ALL_EVENTS_20pct_zh.md').read_text()
    assert atlas.count('### ')==len(primary)
    for eid in primary.event_id:assert atlas.count('### '+eid+' · ')==1,eid
    checks['all_primary_events_have_one_card']=len(primary)
    pred=pd.read_csv(P/'model_predictions.csv');summ=pd.read_csv(O/'model_summary.csv');contrasts=pd.read_csv(O/'model_contrasts.csv')
    for _,row in summ.iterrows():
        a=pred.loc[pred.symbol.eq(row.symbol)&pred.horizon_weeks.eq(row.horizon_weeks)]
        if row.period!='all':a=a.loc[a.period.eq(row.period)]
        err=a[row.model]-a.actual_return
        assert len(a)==row.n
        np.testing.assert_allclose([np.mean(err**2),np.mean(abs(err)),np.mean(np.sign(a[row.model])==np.sign(a.actual_return))],[row.MSE,row.MAE,row.sign_accuracy],atol=1e-14)
    for _,row in contrasts.iterrows():
        a=pred.loc[pred.symbol.eq(row.symbol)&pred.horizon_weeks.eq(row.horizon_weeks)]
        if row.period!='all':a=a.loc[a.period.eq(row.period)]
        de=(a[row.model]-a.actual_return)**2-(a.price-a.actual_return)**2
        np.testing.assert_allclose(de.mean(),row.delta_MSE_vs_price,atol=1e-14)
    checks['model_summary_rows_reconciled']=len(summ);checks['paired_model_contrast_rows_reconciled']=len(contrasts)
    raw=pd.read_csv(P/'alert_outcomes.csv');s=pd.read_csv(O/'alert_summary.csv')
    for _,row in s.iterrows():
        a=raw.loc[raw.symbol.eq(row.symbol)&raw.rule.eq(row.rule)&raw.horizon_weeks.eq(row.horizon_weeks)&raw.barrier.eq(row.barrier)&raw.period.eq(row.period)]
        assert len(a)==row.alerts
        assert row.mature+row.censored==row.alerts
        assert row.desired_first+row.opposite_first+row.no_barrier==row.mature
        np.testing.assert_allclose(a.future_return.mean(),row.mean_forward_return,atol=1e-14,equal_nan=True)
    checks['all_alert_summary_denominators_reconciled']=len(s)
    agg=pd.read_csv(O/'event_aligned_medians.csv');rawcurves=[]
    panels={sym:pd.read_csv(P/(sym.replace('.','_')+'_panel.csv'),parse_dates=['date']).set_index('date') for sym in primary.symbol.unique()}
    for _,row in agg.iterrows():
        price=[];risk=[]
        for _,e in primary.loc[primary.symbol.eq(row.symbol)&primary.direction.eq(row.direction)].iterrows():
            a=panels[row.symbol];i=a.index.get_loc(pd.Timestamp(e.origin_extreme_date_hindsight));j=i+int(row.tau)
            if j<0 or j>=len(a) or a.segment.iloc[j]!=e.segment:continue
            price.append(a.close.iloc[j]/a.close.iloc[i]-1);risk.append(a.risk_appetite.iloc[j]-a.risk_appetite.iloc[i])
        assert row.n_price==np.isfinite(price).sum() and row.n_risk==np.isfinite(risk).sum()
        np.testing.assert_allclose([row.price_median,row.price_q25,row.price_q75],np.nanquantile(price,[.5,.25,.75]),atol=1e-12)
        np.testing.assert_allclose([row.risk_median,row.risk_q25,row.risk_q75],np.nanquantile(risk,[.5,.25,.75]),atol=1e-12)
    checks['aggregate_curve_rows_from_event_input_windows']=len(agg)
    a=pd.read_csv(O/'overlapping_window_associations.csv')
    for _,x in a.iterrows():
        b=panels[x.symbol]
        if x.period!='all':b=b.loc[b.period.eq(x.period)]
        b=b[[x.proxy,'return20']].dropna()
        assert len(b)==x.n
        np.testing.assert_allclose(spearmanr(b[x.proxy],b.return20).statistic,x.spearman_overlapping_20_observation_windows,atol=1e-12)
    checks['overlap_association_rows_recomputed']=len(a)
    # All displayed precision comes from source result cells, with JSON nulls.
    for record in display['primary_models']:
        row=contrasts.loc[contrasts.symbol.eq(record['symbol'])&contrasts.model.eq(record['model'])&contrasts.horizon_weeks.eq(8)&contrasts.period.eq('all')&contrasts.block_sessions.eq(126)].iloc[0]
        for col in ['n','delta_MSE_vs_price','relative_MSE_change','ci_low','ci_high','holm_adjusted_p']:np.testing.assert_allclose(row[col],record[col],atol=1e-14)
    checks['primary_model_display_cells']=len(display['primary_models'])
    out={'status':'PASS_SAME_AUTHOR_RECONCILIATION','checks':checks,'independent_validation':False,'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (O/'report_reconciliation.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))

if __name__=='__main__':main()
