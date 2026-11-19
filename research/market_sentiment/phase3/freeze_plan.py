"""Freeze analysis choices before inspecting phase-3 financial results."""
from pathlib import Path
import datetime, hashlib, json

ROOT=Path(__file__).resolve().parent
DB=ROOT.parent/'moomoo_research_dataset_20261119/dataset_v0_1/market_research.sqlite'
config={
 'run_id':'phase3_20261119_v1',
 'price_database_sha256':'c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e',
 'symbols_autype':{'US.QQQ':1,'SH.000001':0,'SH.000688':0,'US..SOX':0},
 'start':'1996-01-01','end':'2026-11-17',
 'directional_change_thresholds':[0.20,0.10],
 'primary_threshold':0.20,'midterm_confirmation_calendar_days':182,
 'known_price_holes':{'US..SOX':['2000-01-03','2000-03-01','2000-03-02','2000-03-03','2000-03-06','2000-08-28','2009-08-11']},
 'price_features':{'return_sessions':[20,60],'moving_average_sessions':60,'drawdown_sessions':252,'volatility_sessions':20},
 'proxy_features':{'risk_appetite':'100 * (1 - trailing VIX percentile rank), including current source observation, average ties','rank_sessions':1260,'rank_minimum_sessions':756,'change_sessions':20,'VIX_stress_threshold':30,'VIX_easing_from_21_session_max_fraction':0.20,'VIX_complacency_threshold':15,'VIX_break_from_21_session_min_fraction':0.50},
 'alignment':{'select':'second most recent nonmissing US source observation strictly before local signal date','max_source_age_calendar_days':7,'PIT_certified':False},
 'signals':{'price_up':'close > MA60 AND return20 > 0','price_down':'close < MA60 AND return20 < 0','risk_easing':'21-session maximum VIX >= 30 AND VIX <= 0.8 * that maximum','risk_break':'21-session minimum VIX <= 15 AND VIX >= 1.5 * that minimum','joint_up':'price_up AND risk_easing','joint_down':'price_down AND risk_break','onset':'false-to-true state change, minimum 20 quote sessions since last accepted alert of same rule in same price segment'},
 'outcomes':{'entry':'next observed local daily close','horizons_calendar_weeks':[4,8,12],'primary_weeks':8,'end':'last observed close on/before entry date + horizon; maximum 7 calendar days stale','barriers':[0.10,0.05,0.15],'primary_barrier':0.10,'barrier_measure':'closing prices only, first hit; desired direction versus opposite; none/censored retained'},
 'periods':{'development':['1996-01-01','2010-12-31'],'check':['2011-01-01','2016-12-31'],'late_check':['2017-01-01','2025-12-31'],'monitor':['2026-01-01','2026-11-17']},
 'associations':{'primary':'Spearman lagged proxy level/change vs 8-week forward return','all_proxies':['risk_appetite','vix_change20','credit_change20','rate_change20'],'also':['same-date descriptive risk/price changes, NOT prediction','4/12-week sensitivity'],'controls':'price-only Ridge versus same rows with proxy features'},
 'models':{'estimator':'Ridge with intercept, alpha=1; train-only population standardization','train':'all rows with fully mature labels before January 1 of each test year','test_years':list(range(2011,2027)),'minimum_training_rows':756,'comparisons':['train_mean','price','price_vix','price_funding','price_both'],'price_features':['return20','return60','ma60_gap','drawdown252','volatility20'],'vix_features':['risk_appetite','vix_change20'],'funding_features':['credit_level','credit_change20','rate_change20'],'primary_metric':'paired squared prediction error versus price-only on identical test rows','secondary':['MAE','sign_accuracy'],'not_pristine_holdout':True},
 'uncertainty':{'method':'non-circular moving block bootstrap on chronologically ordered eligible daily rows, overlapping labels; same-index paired loss','block_sessions':[126,63,252],'primary_block':126,'replicates':999,'seed':20261119,'confidence':0.95,'multiplicity':'Holm adjustment within 12 primary market-by-added-model tests; separate 16 primary market-by-proxy Spearman tests','classification':'exploratory, correlated markets and current vintage'},
 'official_events':{'universe':'all Statement anchor links on Federal Reserve annual historical pages 1996-2020, and FOMC calendars for 2021-2026; store annual fetch failures','purpose':'date/source atlas only; no causal labels, no surprise estimates, no dating minutes to meeting dates','China_30_year_complete_news_universe':'NOT_AVAILABLE'},
 'acceptance':'all rule-defined confirmed events and open segments retained; no universal reversal claim; all model variants/horizons/periods and failed or insufficient cells saved; no raw vendor market series published'
}
assert hashlib.sha256(DB.read_bytes()).hexdigest()==config['price_database_sha256']
for r in json.loads((ROOT/'raw/input_manifest.json').read_text()):
 assert r['status']=='CAPTURED_CURRENT_VINTAGE'
 assert hashlib.sha256((ROOT/'raw'/f"{r['series']}.csv").read_bytes()).hexdigest()==r['sha256']
for name in ['config.json','freeze_receipt.json']:
 assert not (ROOT/name).exists(),f'Immutable freeze already exists: {name}'
(ROOT/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
receipt={'frozen_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'financial_results_calculated_before_freeze':False,'config_sha256':hashlib.sha256((ROOT/'config.json').read_bytes()).hexdigest(),'input_manifest_sha256':hashlib.sha256((ROOT/'raw/input_manifest.json').read_bytes()).hexdigest(),'price_database_sha256':config['price_database_sha256'],'method_choice':'Fixed operational candidate definitions; not a known universal market law'}
(ROOT/'freeze_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
print(json.dumps(receipt))
