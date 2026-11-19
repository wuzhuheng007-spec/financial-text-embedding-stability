from pathlib import Path
import hashlib,json,datetime
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parent
PIN='7a0165f324f06a0f88aafc253de18c63431d2a9d'
BASE='https://github.com/BWInvestorSentimentIndex/BakerandWurglerInvestorSentimentIndex/blob/'+PIN+'/'

def main():
    d=ROOT/'results/bw_diagnostic';diag=json.loads((d/'summary.json').read_text());check=json.loads((d/'self_check.json').read_text())
    rev=json.loads((ROOT/'results/revision_audit/summary.json').read_text());news=json.loads((ROOT/'results/news_field_pilot/summary.json').read_text())
    assert check['status']=='PASS'
    matrix=[
      {'id':'BW01','fact':'当前维护版本使用五个情绪代理，已去掉NYSE成交量换手代理','status':'VERIFIED_VERSION_SPECIFIC','source':'README.md','lines':'24;36-37','interpretation':'不是直接复现2006论文原始六代理版本'},
      {'id':'BW02','fact':'作者明确不建议以发布序列相邻期差分衡量情绪变化','status':'VERIFIED_RESTRICTION','source':'README.md','lines':'27-28','interpretation':'本研究不把SENT(t)-SENT(t-1)当作拐点因子'},
      {'id':'BW03','fact':'当前脚本在196507至202512范围估计标准化、正交化回归及PCA','status':'VERIFIED_CODE_WINDOW','source':'StataCode/1. Create Baker-Wurgler Sentiment.do','lines':'28-29;137-160;196','interpretation':'历史日期的发布分数使用了更晚的拟合样本；本轮未估计其对预测表现的影响'},
      {'id':'BW04','fact':'2026修订正交化宏观处理为移动平均去趋势偏离','status':'VERIFIED_SOURCE_AND_MEMO','source':'StataCode/1. Create Baker-Wurgler Sentiment.do; BW INDEX DETREND.pdf','lines':'58-98; memo pp1,3-4','interpretation':'不能只根据README旧宏观列的同比说明重造新版本SENT_ORTH'},
      {'id':'BW05','fact':'工作簿只有月份归属，没有逐期首发/修订可用时刻','status':'MISSING_PUBLICATION_CALENDAR','source':'SENTIMENT.xlsx','lines':'DATA schema and README','interpretation':'往后平移一个月并不能证明当时可得性'},
      {'id':'BW06','fact':'宏观获取脚本直接导入FRED序列，未记录逐次历史vintage参数','status':'VINTAGE_NOT_RECONSTRUCTED','source':'StataCode/0. Download Macro Data.do','lines':'import fred and output block','interpretation':'当前工作簿不能自动充当ALFRED历史发布存档'},
      {'id':'BW07','fact':'GitHub版本中包括无DATA页和截断DATA页的中间文件','status':'VERIFIED_FILE_INVENTORY','source':'SENTIMENT.xlsx commit history','lines':'results/revision_audit/workbook_inventory.csv','interpretation':'提交日期不是首次公开时间；不能默认每个文件版本都有完整历史'}
    ]
    pd.DataFrame(matrix).to_csv(ROOT/'results/availability_matrix.csv',index=False)
    registry=json.loads((ROOT/'source_audit/source_registry_complete.json').read_text())
    history=json.loads((ROOT/'source_audit/revision_history.json').read_text())
    citations={'checked_date_client':'2026-11-19','author_commit':PIN,'source_snapshots':registry,'earlier_workbooks':history['snapshots'],
      'official_sources':[{'id':'FRED_RT','url':'https://fred.stlouisfed.org/docs/api/fred/realtime_period.html','scope':'Current FRED versus historical ALFRED vintages','opened':True},
      {'id':'FRED_USREC','url':'https://fred.stlouisfed.org/series/USREC','scope':'NBER-based dummy convention, not direct contemporary public knowledge','opened':True},
      {'id':'FED2019','url':'https://www.federalreserve.gov/newsevents/pressreleases/monetary20190130a.htm','scope':'Official declared release14:00EST and patient policy wording','opened':True},
      {'id':'FED2020','url':'https://www.federalreserve.gov/newsevents/pressreleases/monetary20200323a.htm','scope':'Official declared release08:00EDT and market-functioning purchase policy','opened':True},
      {'id':'CN2024','url':'https://www.sse.com.cn/home/component/news/c/c_20240924_10762338.shtml','scope':'Conference starts09:00Beijing, not exact full-transcript availability','opened':True}],
      'failed_accesses':[{'url':'https://www.nber.org/news/business-cycle-dating-committee-announcement-june-8-2020','status':'403; not used for a specific historical announcement-date claim'},{'url':'https://www.nber.org/sites/default/files/2020-11/june2020.pdf','status':'403; excluded from support'}],
      'memo_scope':'Pages1-4 inspected by extraction, page3 also visually reviewed; no claim to independently reproduce memo correlations.'}
    (ROOT/'citation_audit.json').write_text(json.dumps(citations,ensure_ascii=False,indent=2),encoding='utf-8')
    compare=diag['published_comparison'];prefix=pd.DataFrame(diag['prefix_summary'])
    pt='\n'.join(f'| {int(r.fit_end_yearmo)} | {int(r.shared_historical_months)} | {r.correlation_with_full_window:.8f} | {r.mean_absolute_score_revision:.6f} | {r.max_absolute_score_revision:.6f} |' for r in prefix.itertuples(index=False))
    inv='\n'.join(f'| {x["date"][:10]} | `{x["commit"][:12]}` | {x["data_rows"]} | {x.get("last_yearmo") or "—"} | {x["status"]} |' for x in rev['inventory'])
    choices=[x for x in rev['comparisons'] if x['old_commit'] in ['cbf39c061ff9b38e2d85f5499d8be2bbb2334f2d','cc8c4df8dc8273e1a981f396657b02e35fc5e927']]
    vt='\n'.join(f'| {x["old_commit_date"][:10]} | {x["measure"]} | {x["paired_months"]} | {x["mean_absolute_revision"]:.6f} | {x["max_absolute_revision"]:.6f} | {x["correlation"]:.8f} |' for x in choices)
    plt.rcParams['svg.hashsalt']='bw-audit-phase2-20261119'
    fig,ax=plt.subplots(figsize=(8,4));a=prefix[prefix.fit_end_yearmo!=202512]
    bars=ax.bar(a.fit_end_yearmo.astype(str),a.mean_absolute_score_revision,color=['#2563eb','#0891b2','#0d9488'])
    for bar,val in zip(bars,a.mean_absolute_score_revision):ax.text(bar.get_x()+bar.get_width()/2,val+.001,f'{val:.4f}',ha='center')
    ax.set_ylabel('Mean absolute standardized-score difference');ax.set_xlabel('Prefix fit cutoff (year-month)');ax.set_title('Historical scores depend on the estimation window',loc='left');ax.grid(axis='y',alpha=.15)
    fig.text(.06,.015,'Same current-vintage inputs; full-window reference ends202512. Construction diagnostic, not return performance.',fontsize=8)
    fig.tight_layout(rect=[0,.05,1,1]);fig.savefig(ROOT/'prefix_sensitivity.svg',metadata={'Date':None});plt.close(fig)
    body=f'''# 第二阶段：BW数据可用性、版本与构造审核

日期：2026-11-19（北京时间）。研究状态：**结构和数值审核已执行；历史实时可用性与情绪预测效力尚未建立。**

## 本轮结论

1. 作者明确将当前维护的SENT/SENT_ORTH视作低频水平指标，不建议把发布序列的简单差分解释为情绪变化。[作者README]({BASE}README.md#L24-L30) 因此本项目保留BW作为水平状态参照的候选，不把其相邻期差分直接包装成情绪拐点。
2. 源码在完整历史区间估计标准化、正交化及PCA，当前版本另含2026年的宏观处理修订。文件的月份归属不等于当时发布时间；延后一个月不能同时修复发布、修订和拟合窗口的问题。[固定源码]({BASE}StataCode/1.%20Create%20Baker-Wurgler%20Sentiment.do#L137-L196)
3. 五代理构造与当前发布SENT数值非常接近，但这只检验当前版本的构造一致性，不证明当时可得、预测有效或SENT_ORTH已复现。
4. 已检查作者仓库该工作簿路径的全部{rev['tracked_workbook_versions_inspected']}个返回版本，发现真实的历史数值修订，也保留了无DATA页、数据截断的中间上传记录。Git提交时间不是首次向市场公开的时间。
5. 三条官方政策资料完成字段试点：美国公告有明确声明的发布时间，中国发布会的开始时间与完整文本可用时刻分开。预期差、反应残差及收益模型均未计算。

## 计划与实际执行

| 步骤 | 实际执行 | 证据 |
|---|---|---|
| S0 既有研究同步 | 价格数据验收、phase0/phase1结论和代码已归档；首提交89ce71ac0038e97bbf8c1f95138791501b33bca6，关键远端文件读回一致 | 总目录JOURNAL.md、PUBLICATION_ADAPTATIONS.json |
| S1 作者源码与版本 | 固定作者提交{PIN}；核对README、3份Stata文件、备忘录和工作簿；追加检查所有早期工作簿版本 | citation_audit.json、availability_matrix.csv、revision_audit |
| S2 数值构造诊断 | 依冻结方案执行原始代理PC1与发布SENT对照、四个前缀拟合；用直接滚动计算及SVD自检 | bw_diagnostic/summary.json、self_check.json |
| S3 新闻字段试点 | 实际抓取{news['captured_current_source_pages']}个当前官方页面并记录哈希；无预期时保持null | news_field_pilot/events.json、summary.json |
| S4 持续记录 | 本报告、结果、限制、下一步进入研究日志；远端同步以本轮最终提交回执为准 | 总目录steps.json、STATUS.json及Git历史 |

工作簿版本历史检查是S1的追加诊断：使用该路径返回的所有历史版本，不按差异大小选版本，不改变S2冻结方案。此前其他个人平台/新闻模型记录维持原状态，本轮没有重跑或重新认证那些实验。

## 源码说明的使用边界

| 观察到的内容 | 对研究的含义 |
|---|---|
| 原六代理版本中的NYSE换手代理已移除，当前为五代理 | 论文原始版本、当前发布数据和自行重构必须分别命名 |
| 代理存在12个月平滑/滞后 | 月差未必是即时情绪变化，不能按相邻点变化解释 |
| 标准化、正交化和PCA使用传入的整段区间 | 当前全历史分数不是每个历史时点重新拟合所得 |
| 2026年的SENT_ORTH采用宏观去趋势偏离 | README旧宏观列仍有同比措辞；复现应绑定实际代码版本 |
| 宏观从FRED导入，没有本轮历史vintage恢复 | FRED说明默认历史为今天知道的信息；ALFRED可用于指定历史可用版本。[官方说明](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html) |
| USREC是按NBER周期解释生成的虚拟变量 | 不能把标记的历史月份自动当成该月已公布的状态。[FRED说明](https://fred.stlouisfed.org/series/USREC) |

备忘录的基期效应和12个月移动平均去趋势说明已查阅；本轮没有独立重算其文中相关系数，也不引用该数值作为自身结果。NBER具体历史公告原文访问403，不用检索摘要冒充已读原文。

## 当前版本数值对照

NYU与固定GitHub工作簿字节哈希不同，但两者DATA表的{len(pd.read_excel(ROOT/'source_audit/SENTIMENT_github.xlsx',sheet_name='DATA'))}行、15列数值和缺失位置一致。README标签为2026年3月，不能据此补造每个月的实际发布日期。

对五条原始代理执行12个月IPO数量合计、数量加权首日收益及规定滞后、相关矩阵PC1和样本标准化，以IPO数量载荷为正固定符号。与发布SENT有{compare['n_paired_months']}个共同有效月，均方根差为**{compare['rmse']:.3e}**，最大绝对差为**{compare['max_absolute_difference']:.3e}**。代码、数据、原始代理和输出哈希在运行回执中；本轮未运行Stata，不能称完整官方程序认证复现。

改变拟合截止、保持同一当前版本输入：

| 拟合截止YYYYMM | 共同历史月数 | 与全窗口相关 | 平均绝对分数差 | 最大绝对分数差 |
|---|---:|---:|---:|---:|
{pt}

单位为标准化指标分数，不是收益率或百分点。差异包含标准化尺度与PCA载荷变化，不能把它解释为真实情绪修订量、虚假超额收益或预测影响。相关系数很高既不能证明历史可用性，也不能证明该构造问题产生了多大投资偏差。

![前缀诊断](prefix_sensitivity.svg)

## 作者公开工作簿的版本记录

| Git记录日期UTC | 提交前缀 | DATA行数 | 最后月份 | 检查状态 |
|---|---|---:|---:|---|
{inv}

2026-04-01文件只有README；2026-04-03文件只含到1972-01的DATA，本轮没有把它们静默当作完整历史。当前4月21日提交说明为更正输出上传。工作簿有缺失/短缺与作者经济研究结论是否成立是不同问题。

早期文件与当前文件在同一个历史月份的数值对照：

| 旧Git记录日期 | 指标 | 成对月份 | 平均绝对版本差 | 最大绝对版本差 | 相关 |
|---|---|---:|---:|---:|---:|
{vt}

这是版本间同月份的审核，不是相邻月份情绪变化指标。差异混合了数据修订、样本延长、处理方法和输出更正，不能归因于其中单个原因。现有版本记录集中在2025—2026年，未恢复几十年的逐月发布存档。

## 官方新闻字段试点

| 事件 | 记录的UTC时刻 | 时刻的含义 | 尚缺什么 |
|---|---|---|---|
| 2019-01-30 FOMC声明 | 19:00 | 原声明14:00 EST发布安排 | 事前预期、历史文本版本及完整事件池 |
| 2020-03-23 FOMC声明 | 12:00 | 原声明08:00 EDT发布安排 | 预期强度、同期冲击与无选择偏差样本 |
| 2024-09-24中国政策发布会 | 01:00 | 北京09:00会议开始；完整文字首发时刻为null | 各政策发言时刻、预期与文本首发证据 |

来源：[Fed2019](https://www.federalreserve.gov/newsevents/pressreleases/monetary20190130a.htm)、[Fed2020](https://www.federalreserve.gov/newsevents/pressreleases/monetary20200323a.htm)、[国新办发布会原文转载](https://www.sse.com.cn/home/component/news/c/c_20240924_10762338.shtml)。这是有官方时间依据的历史归档样本，不是本研究在当年采到的实时记录。{news['events']}条说明性事件不足以验证因子；预期差、好坏标签、反应得分和金融拟合均为未计算，事件缺失字段保存null。

## 接下来如何推进

1. BW发布序列用于低频水平对照的候选；不直接求差分来判情绪拐点。若采用自行构建的扩展窗口代理，另立名称，并逐项处理原始数据修订和可用时刻。
2. 从真实当前抓取开始保留新版本，作为未来可验证的可用性记录；旧发布时刻缺失保持未知，不回填成月末。
3. 新闻路径扩大为事先确定的完整日期事件池，分别构造政策语气/已知预期差和市场反应残差；先做价格控制，再评估4/8/12周增量。当前仓库已有个股和短期语气开发记录，本轮不把它们冒充本研究的三十年大盘新闻输入。

## 审核结论

数值审核由同一执行方完成：直接计算IPO滚动权重，使用SVD与特征值分解交叉核对，并检查未来输入扰动不影响截止前重拟合结果。自检通过；这不是外部独立验证。本轮没有市场收益回归、没有策略回测，没有取得BW预测拐点有效性的证据。

证据链：源提交/工作簿哈希 → 固定PLAN和freeze_receipt → diagnose_bw.py/audit_revisions.py → 保存结果 → 本报告；新闻字段另由news_field_pilot.py及源快照哈希支持。所有旧结果保持不动。
'''
    (ROOT/'Phase2_BW_Availability_Report_zh.md').write_text(body,encoding='utf-8')
    final={'Final status':'PARTIALLY_SUPPORTED','Plan–execution gate':'PASS','Experimental sufficiency gate':'LIMITED','Result provenance gate':'PASS','Citation verification gate':'PASS','Commands actually run':5,'command_count_scope':'Executed primary programs: diagnose_bw.py, self_check_bw.py, news_field_pilot.py, audit_revisions.py, build_report.py; excludes retrieval/packaging diagnostics','Authoritative artifacts inspected':15,'artifact_count_scope':'Six pinned author source files, four earlier workbooks, five opened official web documents; old reports and derived files excluded','Unresolved blockers':['Historical BW monthly release/vintage archive absent','Complete expectations/news event universe absent','No independent audit or new future outcomes'],'Claims authorized':'Scoped source/version constraints, current-vintage numerical construction match, prefix sensitivity, observed file revisions and three-event field feasibility','Claims not authorized':'BW first-difference sentiment factor, historical tradable PIT replication, stable predictive improvement, causal price effect','Worktree/user data preserved':'YES','code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'source_commit':PIN,'self_check':'results/bw_diagnostic/self_check.json','news_pilot':'results/news_field_pilot/summary.json','fitting_scope':'Unsupervised construction diagnostic only, no financial estimators'}
    (ROOT/'AUDIT_FINAL.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
    ledger={'status':'PARTIALLY_SUPPORTED','source_claims':'results/availability_matrix.csv','citation_audit':'citation_audit.json','numerical_claims':[{'claim':'Published SENT numerical comparison','evidence':'results/bw_diagnostic/published_comparison.json'},{'claim':'Prefix cutoff sensitivity','evidence':'results/bw_diagnostic/prefix_sensitivity.csv'},{'claim':'Version history differences and incomplete files','evidence':'results/revision_audit/workbook_inventory.csv; published_version_comparison.csv'},{'claim':'Official event field counts','evidence':'results/news_field_pilot/events.json; summary.json'}],'immutable_inputs':['NYU workbook SHA2563580398aaba6b72ad988844416c7b0ac9caca24fd2ab1d9149a983ec63b9f7a2','Pinned author commit'+PIN],'no_return_test':True,'same_author_self_check':True}
    (ROOT/'evidence_ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2))
    print(json.dumps({'report':'Phase2_BW_Availability_Report_zh.md','status':'PARTIALLY_SUPPORTED','prefix_fits':len(prefix),'versions':rev['tracked_workbook_versions_inspected'],'news_events':news['events']}))

if __name__=='__main__':main()
