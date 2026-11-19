"""Read-only review of saved evidence; no financial estimator or return rerun."""
from pathlib import Path
import argparse,datetime,hashlib,json
import pandas as pd

HERE=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--workspace',type=Path,required=True);a=ap.parse_args();base=a.workspace.resolve()
    p4=base/'sentiment_phase4_20261119';p3=base/'sentiment_phase3_20261119'
    config=json.loads((p4/'config.json').read_text());manifest=json.loads((p4/'results/run_manifest.json').read_text());old_check=json.loads((p4/'results/self_check.json').read_text())
    assert sha(p4/'config.json')==manifest['config_sha256']==json.loads((p4/'freeze_receipt.json').read_text())['config_sha256']
    assert sha(p4/'analyze.py')==manifest['code_sha256']
    assert sha(p4/'check.py')==old_check['check_code_sha256']
    assert sha(p4/'results/run_manifest.json')==old_check['run_manifest_sha256']
    assert sha(p3/'results_v1_1/run_manifest.json')==manifest['parent_run_manifest_sha256']
    for n,h in manifest['outputs'].items():assert sha(p4/n)==h,n
    for x in json.loads((p4/'input_manifest.json').read_text()):assert sha(p4/'raw'/f"{x['series']}.csv")==x['sha256']
    fits=json.loads((p4/'private/fits.json').read_text())
    for f in fits:
        assert f['train_n']>=config['models']['minimum_train']
        assert pd.Timestamp(f['latest_label'])<pd.Timestamp(f['year'],1,1)
        assert f['features']==(config['models']['base']+([] if f['model']=='base' else config['models'][{'base_breadth':'add_breadth','base_real':'add_real','base_joint':'add_joint'}[f['model']]]))
        assert f['alpha'] in config['models']['alpha']
    contrasts=pd.read_csv(p4/'results/model_contrasts.csv');primary=contrasts.loc[contrasts.horizon_weeks.eq(8)&contrasts.alpha.eq(1)&contrasts.period.eq('all')&contrasts.block.eq(126)]
    assert len(primary)==12 and primary.holm_p.notna().all()
    references=['config.json','freeze_receipt.json','input_manifest.json','analyze.py','check.py','results/run_manifest.json','results/self_check.json','results/model_contrasts.csv','private/fits.json']
    rows=[
      {'id':'D01','item':'数据及结果身份','status':'PASS_WITHIN_DECLARED_SCOPE','evidence':['phase4/config.json','phase4/results/run_manifest.json'],'meaning':'本轮比对源文件/配置/代码与回执哈希一致，不等于认证供应商内容或历史PIT'},
      {'id':'D02','item':'信息可用性/PIT','status':'PARTIAL','evidence':['phase4/config.json','phase4/input_manifest.json'],'meaning':'滞后来源日期已保存且原自查记录存在；当前版本、真实历史发布版本与所有首次发布时间未恢复'},
      {'id':'D03','item':'训练与标签分离','status':'PASS_SAVED_METADATA_ONLY','evidence':['phase4/analyze.py','phase4/results/self_check.json'],'meaning':'本轮核对保存模型标签末日早于测试年初；未重拟合。本轮代码阅读确认训练窗标准化；不是所有泄漏的排除证明'},
      {'id':'D04','item':'样本与可交易性','status':'PARTIAL_DIAGNOSTIC_ONLY','evidence':['phase4/results/coverage.json','phase4/PLAN_zh.md'],'meaning':'已记录接受行情及不一致日期；实际可交易ETF选择、完整日历核对与可成交报价未完成'},
      {'id':'D05','item':'风格与增量控制','status':'PARTIAL','evidence':['phase4/config.json','phase4/results/model_contrasts.csv'],'meaning':'有价格/VIX/信用/名义利率强基线的同样本比较；不是股票横截面行业市值中性化；不宣称风格因果已分离'},
      {'id':'D06','item':'显著性与重叠窗口','status':'PARTIAL','evidence':['phase4/results/model_contrasts.csv','phase4/config.json'],'meaning':'预定块自助法和主家族Holm已执行；不能解释为独立危机样本充分或研究全程选择偏差已消除'},
      {'id':'D07','item':'成本/换手/容量/扣费Sharpe','status':'NOT_RUN_IN_PHASE3_OR_PHASE4','evidence':['phase4/AUDIT_FINAL.json'],'meaning':'第三四阶段未实施可交易组合收益回测；其他平台基线模拟成本不能移作本阶段证据'},
      {'id':'D08','item':'敏感性与分期','status':'EXECUTED_LIMITED','evidence':['phase4/results/model_contrasts.csv','phase4/results/model_summary.csv'],'meaning':'保存两alpha、三期限和不同块/时期；仍仅一个模型族、相关市场、部分短样本'},
      {'id':'D09','item':'未见样本外/独立复制','status':'NOT_ESTABLISHED','evidence':['phase4/config.json','phase4/results/self_check.json'],'meaning':'逐年预测是时序检查，历史已看过且为当前版本；同作者自查不是独立审计'},
      {'id':'D10','item':'正式因子准入','status':'NOT_QUALIFIED','evidence':['phase4/results/model_contrasts.csv','phase4/AUDIT_FINAL.json'],'meaning':'尚无稳健增量，未认证为实盘因子；图示经验阈值不能推翻这个状态'}]
    snapshot={'mode':'READ_ONLY_SAVED_ARTIFACT_REVIEW_NO_ESTIMATOR_FIT','status':'PASS_FOR_IDENTITY_AND_LISTED_METADATA_CHECKS','finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'config_sha256':sha(p4/'config.json'),'run_manifest_sha256':sha(p4/'results/run_manifest.json'),'previous_self_check_status':old_check['status'],'previous_self_check_not_rerun':True,'saved_fits_metadata_checked':len(fits),'primary_comparison_rows_checked':len(primary),'primary_rows_adjusted_p_lt_005_and_negative_delta':int((primary.holm_p.lt(.05)&primary.delta_MSE.lt(0)).sum()),'original_output_hashes_checked':len(manifest['outputs']),'source_file_hashes':{str((p4/n).relative_to(base)):sha(p4/n) for n in references},'readiness_items':len(rows),'independent_validation':False,'review_code_sha256':sha(__file__)}
    (HERE/'snapshot.json').write_text(json.dumps(snapshot,indent=2));(HERE/'readiness.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));print(json.dumps(snapshot,ensure_ascii=False))
if __name__=='__main__':main()
