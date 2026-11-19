from pathlib import Path
import json, hashlib, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

ROOT=Path(__file__).resolve().parent
RES=ROOT/'results'
NAMES={'US.QQQ':'QQQ','SH.000001':'上证综指','SH.000688':'科创50','US..SOX':'SOX'}
ORDER=['US.QQQ','SH.000001','SH.000688','US..SOX']
ENG={'US.QQQ':'QQQ','SH.000001':'SSE Composite','SH.000688':'STAR 50','US..SOX':'SOX'}

def pct(x,sign=False):
    return ('—' if pd.isna(x) else format(float(x)*100,'+.2f' if sign else '.2f')+'%')
def pp(x):return '—' if pd.isna(x) else f'{float(x)*100:+.3f}'
def interval(r):
    return '样本不足，未给区间' if pd.isna(r.ci95_low_b26) else f'[{r.ci95_low_b26*100:+.2f}, {r.ci95_high_b26*100:+.2f}]'

def main():
    cfg=json.loads((ROOT/'config.json').read_text());run=json.loads((RES/'run_manifest.json').read_text())
    selfcheck=json.loads((RES/'self_check.json').read_text());audit=json.loads((ROOT/'citation_audit.json').read_text())
    sources={s['id']:s for s in audit['sources']}
    stats=pd.read_csv(RES/'baseline_summary.csv');contrasts=pd.read_csv(RES/'state_contrasts.csv')
    panel=pd.read_csv(RES/'weekly_panel.csv');cycles=pd.read_csv(RES/'cycles_all_thresholds.csv')
    bw=json.loads((ROOT/'external_input_probe'/'BW_schema_audit.json').read_text())
    assert selfcheck['status']=='PASS'
    for name,want in run['outputs'].items():
        assert hashlib.sha256((RES/name).read_bytes()).hexdigest()==want
    primary=stats[(stats.segment=='temporal_check_2')&(stats.horizon_calendar_weeks==8)]
    table=[];top=[]
    for symbol in ORDER:
        allr=primary[(primary.symbol==symbol)&(primary.state=='all')].iloc[0]
        r=primary[(primary.symbol==symbol)&(primary.state=='weak_rebounding')].iloc[0]
        table.append(f'| {NAMES[symbol]} | {int(r.signal_weeks)} / {int(r.contiguous_signal_runs)} | {pct(r.mean_price_change)} | {pct(allr.mean_price_change)} | {pp(r.effect_vs_all)} | {interval(r)} |')
        r=primary[(primary.symbol==symbol)&(primary.state=='strong_fading')].iloc[0]
        top.append(f'| {NAMES[symbol]} | {int(r.signal_weeks)} | {pct(r.mean_price_change)} | {pp(r.effect_vs_all)} | {interval(r)} |')
    # All primary cells have retained full-precision source rows.
    primary.to_csv(RES/'primary_display_cells.csv',index=False)
    fig,axes=plt.subplots(1,2,figsize=(12.5,4.8),sharey=True)
    for ax,state,title,color in zip(axes,['weak_rebounding','strong_fading'],['Weak trend + recent rebound','Strong trend + recent decline'],['#2563eb','#b45309']):
        for i,symbol in enumerate(ORDER):
            r=primary[(primary.symbol==symbol)&(primary.state==state)].iloc[0];x=r.effect_vs_all*100
            if pd.notna(r.ci95_low_b26):
                ax.hlines(i,r.ci95_low_b26*100,r.ci95_high_b26*100,color=color,lw=2)
                ax.plot(x,i,'o',color=color,ms=7)
            else:ax.plot(x,i,'x',color='#64748b',ms=8)
            ax.annotate(f'n={int(r.signal_weeks)}',(x,i),xytext=(4,9),textcoords='offset points',fontsize=8)
        ax.axvline(0,color='#475569',lw=1,ls='--');ax.set_title(title,fontsize=12)
        ax.grid(axis='x',alpha=.15);ax.set_xlabel('Mean change minus all eligible weeks (percentage points)')
        ax.set_yticks(range(4),[ENG[x] for x in ORDER]);ax.set_ylim(3.65,-.65)
        ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Price-state baseline: 8-calendar-week forward price change',fontsize=15,fontweight='bold',y=.99)
    fig.text(.06,.89,'2017–2025 checks; STAR 50 starts 2021. 95% paired-block intervals (26 observed weeks).',fontsize=9,color='#475569')
    fig.text(.06,.025,'Changes start at the next observed weekly close. Descriptive current-vintage data; no sentiment or strategy validation.',fontsize=9,color='#475569')
    fig.tight_layout(rect=[.01,.08,.99,.86]);fig.savefig(ROOT/'baseline_comparison.png',dpi=170,facecolor='white');plt.close(fig)
    # Fixed historical example illustrates that a confirmed local rally can fail later.
    q=panel[(panel.symbol=='US.QQQ')&(panel.week_label>='2022-01-01')&(panel.week_label<='2023-06-30')].copy()
    q['d']=pd.to_datetime(q.week_label);base=q.close.iloc[0]
    e=cycles[(cycles.symbol=='US.QQQ')&(cycles.threshold==.2)&(cycles.extremum_date>='2022-01-01')&(cycles.extremum_date<='2022-12-31')]
    fig,ax=plt.subplots(figsize=(12.5,5.3));ax.plot(q.d,q.close/base*100,color='#1e3a8a',lw=1.6)
    for k,(_,r) in enumerate(e.iterrows()):
        t=pd.Timestamp(r.extremum_week);v=r.extremum_close/base*100;ct=pd.Timestamp(r.confirmation_week);cv=r.confirmation_close/base*100
        color='#059669' if r.type=='trough' else '#dc2626'
        ax.plot(t,v,'v' if r.type=='trough' else '^',color=color,ms=8)
        ax.plot(ct,cv,'s',color='#64748b',ms=5)
        ax.annotate('',xy=(ct,cv),xytext=(t,v),arrowprops={'arrowstyle':'->','linestyle':':','color':color,'alpha':.65})
        offset=(0,-44) if k in [0,2] else (0,36)
        ax.annotate(f'{r.type}: {r.extremum_date}\nconfirmed: {r.confirmation_date}',(t,v),xytext=offset,textcoords='offset points',ha='center',fontsize=9,color=color)
    ax.set_title('QQQ in 2022: a 20% local recovery preceded another down-leg',loc='left',fontsize=15,fontweight='bold')
    ax.set_ylabel('Forward-adjusted price, first plotted week = 100');ax.grid(alpha=.16)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2));ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.spines[['top','right']].set_visible(False);ax.margins(y=.30)
    fig.text(.06,.018,'Extrema are retrospective weekly-close labels. Squares mark later threshold confirmations; these are not real-time bottom calls.',fontsize=9,color='#475569')
    fig.tight_layout(rect=[.01,.05,.99,.99]);fig.savefig(ROOT/'qqq_2022_local_reversals.png',dpi=170,facecolor='white');plt.close(fig)
    selected=json.loads((ROOT/'case_registry.json').read_text())['cases'];case_lines=[];case_rows=[]
    for c in selected:
        z=cycles[(cycles.symbol==c['market'])&(cycles.threshold==.2)&(cycles.extremum_date>=c['start'])&(cycles.extremum_date<=c['end'])]
        for _,r in z.iterrows():case_rows.append({'case_id':c['id'],'source_id':c['source'],**r.to_dict()})
        detail='；'.join(('低' if r.type=='trough' else '高')+f' {r.extremum_date} → 确认 {r.confirmation_date}' for _,r in z.iterrows()) or '该窗口内没有符合20%规则的已确认极值'
        case_lines.append(f'| {c["id"]} {NAMES[c["market"]]} {c["start"][:4]}—{c["end"][:4]} | {detail} | {c["focus"]} [{c["source"]}] |')
    pd.DataFrame(case_rows).to_csv(RES/'illustrative_case_price_matches.csv',index=False)
    refs='\n'.join(f'- [{s["id"]}] [{s["title"]}]({s["url"]})；{s["authors_or_institution"]}；{s["venue_year"]}。核验范围：{s["scope"]}。' for s in audit['sources'])
    body=f'''# 中期市场情绪拐点研究：第一阶段结果

日期：2026-11-19（北京时间）。输入：moomoo 研究价格数据集 v0.1；日线截止 2026-11-17，本轮完整周截至 2026-11-13。

**本轮已固定研究规则，执行价格基线和全部阈值拐点扫描，完成历史案例与原始文献对照。结果尚不足以支持统一的价格反转规则；新闻情绪因子仍待计算与验证。**

## 1. 实际完成了什么

| 计划 | 实际产物 | 状态 |
|---|---|---|
| 先固定拐点、窗口和分段规则 | PROTOCOL_zh.md、config.json、freeze_receipt.json，在结果计算前保存 | 完成；研究者制定，不是公开注册或盲测 |
| 四条代理分别计算价格状态及4/8/12自然周结果 | results/weekly_panel.csv、baseline_summary.csv、state_contrasts.csv | 完成，保留所有状态、分段和窗口 |
| 扫描事后极值及确认时间 | results/cycles_all_thresholds.csv，20%主版本、10%敏感性 | 完成，包含反向拐点，末端未确认区间不当已知拐点 |
| 核对历史背景和热门因子 | citation_audit.json、case_registry.json | 首轮完成：{len(audit['sources'])}份直接查阅资料；案例用于解释机制 |
| 原始输入重算与无前视检查 | results/self_check.json | 同一执行方自检通过，不是外部独立审计 |
| 追加检查情绪原始数据入口 | external_input_probe/ | BW工作簿已下载并审计；AAII下载403，未取得历史文件 |

运行生成 {len(stats)} 行状态统计、{len(contrasts)} 行状态差比较、{len(cycles)} 条两种阈值合计的已确认极值。它们共享日期、标的和未来窗口，不能当作独立实验或独立危机的数量。旧的 OECD/FRED 月度初筛使用不同输入和定义，本轮未合并，也未把旧结果称为新复现。

## 2. 我们如何定义拐点

**事后标签**采用方向变化扫描：从局部低位上涨20%后，才确认先前的低位；从局部高位回落20%后，才确认先前高位。极值日和确认日分列。这是本轮案例整理的操作定义，阈值会使低波动指数的确认很慢，不能称为统一的情绪反转标准。10%版本完整保留，未按哪个版本更好看选择结论。

**当时可计算的状态**由26个有报价周的均价及4个有报价周的价格变化划分：

| 状态 | 当周能看到的条件 |
|---|---|
| 弱势续跌 | 收盘低于26周均价，最近4个观察周变化≤0 |
| 弱势反弹 | 收盘低于26周均价，最近4个观察周变化>0 |
| 强势续涨 | 收盘不低于26周均价，最近4个观察周变化≥0 |
| 强势转弱 | 收盘不低于26周均价，最近4个观察周变化<0 |

另保存13周动量、52周回撤和13周实现波动率，作为后续控制变量。本阶段未拟合或比较预测模型，也未单独估计这些控制的增量贡献。实现波动率按观察周收益计算并作名义年化；它不等于期权隐含的VIX。[M02]

观察周特征窗口会因整周休市跨越更多自然日，起始日期逐行保存；未来评价严格按4/8/12个自然周定位。信号后下一个观察周的收盘起算，目标日之前最近的观察周收盘作为终点，记录实际起止日期、最多14天端点滞后；未成熟窗口不计算。它是延后观察的价格变化，不是可成交收益、持仓策略或总回报。

## 3. 首轮价格基线：反弹还不足以确认中期改善

以下为协议指定的主展示：2017—2025时间检查段、8自然周窗口。QQQ、上证和SOX合格信号日期为2017-01-06至2025-10-24；科创50为2021-07-23至2025-10-24。均在各自区间内剔除跨界及未成熟标签。这里不是从未见过历史的独立样本外测试。

弱势反弹相对同标的同段全部合格周：

| 标的 | 信号周数 / 连续段数 | 后续均值 | 全部周均值 | 均值差（百分点） | 均值差95%区间（百分点） |
|---|---:|---:|---:|---:|---|
{chr(10).join(table)}

主区间由26观察周的成对循环区块bootstrap产生，2,000次、种子20261119。完整13/52观察周区块敏感性及4/12自然周结果在CSV，不按结果挑窗口。周数和连续段数不是独立事件样本量。

在这组固定规则下，均值差方向并不一致，四个弱势反弹组的主区间都覆盖零。现阶段不能判断“低于中期均线时出现反弹”稳定改善后续分布，也不能把组间差当作情绪的因果贡献。

强势转弱的同一检查：

| 标的 | 信号周数 | 后续均值 | 相对全部周均值差（百分点） | 均值差95%区间（百分点） |
|---|---:|---:|---:|---|
{chr(10).join(top)}

强势内短期下跌同样没有在本配置中形成一致的负向后续差。科创50该组样本低于预设20周门槛，不给区间；不能解释为“确认无效”。这些是描述性检查，未作多重比较校正，不使用显著性发现措辞。

![价格状态基线](baseline_comparison.png)

## 4. 历史对照：极值、确认与政策不是同一个时间

以下是有历史知识背景的说明性案例，并非随机或穷尽的历史事件样本。表中日期是本轮周收盘规则输出，不是盘中或逐日极值，不替代更广市场的官方低点。所有阈值拐点另附完整CSV，未只保留成功反弹。

| 案例窗口 | 周收盘极值 → 后来的20%规则确认 | 要分开研究的机制 |
|---|---|---|
{chr(10).join(case_lines)}

**值得重点追查的两个反例：**

- 美联储2003年报告回顾2002年时，记载部分实际盈利匹配或超过预期，前瞻销售与利润仍不乐观，市场反弹也曾失败。[H02] 因此后续新闻结构化必须把实际财报、公告前预期和指引分开。
- 本轮QQQ20%规则先把2022-06-17的周收盘低位确认于2022-08-12，随后2022-08-12又成为局部高位，于2022-10-14确认下转。20%的局部回升仍可能接着再次下跌；不能把“达到涨幅阈值”直接当作熊市结束。此结论仅指该操作规则下的这条价格序列。

![QQQ2022局部反转](qqq_2022_local_reversals.png)

历史资料支持把信用约束、盈利预期和政策表述单独记录；尚未提供“情绪差到某个程度后固定几周反转”的规律。2015年引用的是深圳监管局记录的强平及分级基金下折投诉，只证明相关约束确实存在，不据此量化全市场卖压或因果。IMF原文本轮访问未完成，未拿检索摘要代替已读原文。

## 5. 热门因子：本轮核到了什么

| 因子路线 | 核验到的研究对象 | 本轮进度及改良约束 |
|---|---|---|
| BW综合情绪 | 原论文主要研究股票横截面，不等于大盘拐点定时器 [L01] | 作者当前版本工作簿已下载；尚未与价格对齐或做效力检验 |
| Aligned Sentiment | 明确面向总市场预测的研究路线 [L02] | 身份和摘要已核；本轮未复现算法。未来所有监督估计须仅用训练窗 |
| AAII多空差 | 每周调查未来六个月方向，当前说明为周四发布 [M01] | 官方历史入口已定位，文件403；不拼接来历不明数据，不作回测声明 |
| VIX水平/变化 | SPX期权隐含的30天预期波动 [M02] | 本轮核验定义，未把它作为方向保证，也未重新检验旧月度结果 |
| 新闻悲观语气 | Tetlock原论文报告日报文本与短期价格压力及随后回归 [L03] | 支持文本研究动机，不能直接推出4/8/12周有效 |
| 消息反应随状态变化 | Boyd等原论文报告失业消息在扩张/收缩下反应不同 [L04] | 支持状态条件化思路；不等于已验证我们拟议的消息反应因子 |

BW原始工作簿已实际检查：SENT有{bw['measures']['SENT']['nonmissing_months']}个非缺失月，1965-07至2025-12；SENT_ORTH有{bw['measures']['SENT_ORTH']['nonmissing_months']}个非缺失月，区间内2025-10缺失，未填值。工作簿说明允许修订，且月末归属不等于当时公开时间，因此不能把当前全历史版本直接当作可交易的逐时点情绪指标。输入身份、字段及读取代码在external_input_probe和audit_external.py。

这项数据入口检查是原计划之外的追加可行性诊断；没有替换价格基线输入或把情绪数据偷偷加入冻结实验。

## 6. 下一阶段的具体因子

先分别研究“利空承受能力”和“利好兑现能力”：事前固定新闻方向、类型、强度和预期差，用仅由过去事件估计的通常反应作参照，观察反应残差是否逐渐变化。周度聚合只使用反应窗口已结束的事件，并把融资约束另列控制。

新闻数据合同已写入NEWS_DATA_CONTRACT_zh.md，要求原文快照、首发/抓取/修订时刻、去重事件、事前预期、价格配对及模型版本。宏观事件和个股事件分开。缺少预期就只测文本语气，不声称“超预期”；不得按后来涨跌给消息定正负。

随后比较价格基线与价格加情绪的同日期、同样本表现。BW首先需要查清月度发布滞后、修订和构造窗口；新闻路线需要真实存档与可用时刻。当前缺口不需要重新运行moomoo授权脚本来解决。

## 7. 自检与结论边界

从原日线重算并核对{selfcheck['weekly_prices_checked']}个观察周、{selfcheck['eligible_feature_rows_checked']}条合格特征、{selfcheck['forward_outcomes_checked']}个成熟未来窗口及{selfcheck['summary_rows_reconciled']}行统计；重算{selfcheck['primary_bootstrap_intervals_recomputed']}个主展示区间；逐条检查{selfcheck['cycle_rows_checked']}条极值记录的价格、方向阈值和确认先后。对四条序列分别将后半段未来价格乘以7，已计算的更早状态均未改变。这里的7是故意扰动的检查参数，不是市场数据。

这仍是同一执行方的自检。四条代理存在市场和行业重叠，不能视为独立市场复制；科创50历史更短。价格来自当前版本，QQQ前复权未认证为总回报。SOX七个已知候选缺口继续保留，不填价格；进入特征或结果窗口则排除，未全面认证的日历仍可能有未发现问题。新周序列从已接受日线重聚合，与供应商周线序列不是同一产物。

**获准的结论：**本轮固定价格状态规则在声明样本内的描述性结果和不确定性；局部阈值反转与最终底部的区别；已直接核验的资料身份和窄范围事实。

**尚未获准的结论：**情绪因子具有稳定预测力、中美共用买卖阈值、真实策略超额收益、政策或IPO对市场的因果贡献、精确见顶见底或当前交易建议。

## 8. 复现与文件

将上一轮数据包中的market_research.sqlite放在可访问位置。依次运行（OUTPUT须为尚不存在的目录；脚本不会覆盖已有结果）：

```powershell
py -3 .\\baseline.py --database PATH_TO_DB --out OUTPUT
py -3 .\\self_check.py --database PATH_TO_DB --results OUTPUT
```

baseline.py与self_check.py依赖numpy、pandas；作图和报告另需matplotlib，工作簿审计另需openpyxl。实际版本与输入、配置、代码、结果哈希见运行回执。重建正式报告时make_report.py使用本包results目录中的固定结果。源数据库不重复打包，身份已固定；不得用新下载的不同版本静默替换。

数据口径、状态、窗口、分段、缺失和bootstrap规则详见PROTOCOL_zh.md及config.json。完整结果不只包含本报告展示的8周窗口。最终数据完整性与计划对应见AUDIT_FINAL.json及evidence_ledger.json。

## 9. 已直接查阅的原始与官方资料

{refs}

资料检索是定向核验，不是完整系统综述；引用只支持列明范围。网页与工作簿当前版本不能自动恢复当时可用信息。

进度：规则冻结✅ → 价格基线✅ → 历史/文献首轮对照✅ → 情绪原始数据审计进行中 → 新闻因子样本外验证待执行。
'''
    (ROOT/'Phase1_Research_Report_zh.md').write_text(body,encoding='utf-8')
    # Machine-readable evidence ledger links every numerical presentation to raw-derived outputs.
    ledger={'status':'PARTIALLY_SUPPORTED','numeric_chain':['report cells / figures','results/primary_display_cells.csv or cycles_all_thresholds.csv','results/weekly_panel.csv','run_manifest.json and config.json','immutable market_research.sqlite and prior row provenance'],
        'run_manifest':'results/run_manifest.json','configuration':'config.json','input_database_sha256':cfg['database_sha256'],
        'claims':[{'id':'N01','claim':'Price-state means, intervals and counts shown in section3','evidence':'results/primary_display_cells.csv','status':'VERIFIED_AT_SCOPED_DESCRIPTIVE_LEVEL'},
          {'id':'N02','claim':'Weak-rebounding effects differ in direction and four primary intervals include zero','evidence':'results/primary_display_cells.csv; filter state=weak_rebounding','status':'VERIFIED_AT_SCOPED_DESCRIPTIVE_LEVEL'},
          {'id':'N03','claim':'Retrospective threshold extrema and confirmation dates','evidence':'results/cycles_all_thresholds.csv; illustrative_case_price_matches.csv','status':'VERIFIED_UNDER_FROZEN_OPERATIONAL_DEFINITION'},
          {'id':'N04','claim':'All figure inputs','evidence':'results/primary_display_cells.csv; weekly_panel.csv; cycles_all_thresholds.csv','status':'VERIFIED_FROM_SAVED_RESULTS'},
          {'id':'D01','claim':'BW workbook nonmissing dates and missing month','evidence':'external_input_probe/BW_schema_audit.json; BW_SENTIMENT.xlsx; audit_external.py','status':'VERIFIED_CURRENT_VINTAGE_ONLY'},
          {'id':'Q01','claim':'No price signal or news sentiment efficacy established','evidence':'PROTOCOL_zh.md; run_manifest.json; NEWS_DATA_CONTRACT_zh.md','status':'INCONCLUSIVE_FOR_PREDICTIVE_CLAIM'}],
        'citations':'citation_audit.json','self_check':'results/self_check.json','independent_validation':False,
        'plan_execution':[{'id':'P1','status':'PASS','artifact':'freeze_receipt.json'}, {'id':'P2','status':'PASS','artifact':'results/run_manifest.json'}, {'id':'P3','status':'PASS','artifact':'results/self_check.json'}, {'id':'P4','status':'PASS_AT_STATED_SCOPE','artifact':'citation_audit.json'}, {'id':'P5','status':'PASS','artifact':'Phase1_Research_Report_zh.md'}, {'id':'P6_EXTRA','status':'BW_AUDITED_AAII_ACCESS_INCOMPLETE','artifact':'external_input_probe/access_results.json','deviation':'Added feasibility diagnosis only; frozen analysis inputs unchanged'}]}
    (ROOT/'evidence_ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2))
    final={'Final status':'PARTIALLY_SUPPORTED','Plan–execution gate':'PASS','Experimental sufficiency gate':'LIMITED','Result provenance gate':'PASS','Citation verification gate':'PASS',
      'Commands actually run':5,'command_count_scope':'Successful main-program invocations only: baseline.py once, self_check.py once, audit_external.py once, make_report.py twice (second invocation only narrows report wording, with no new analysis). Retrieval and shell diagnostics excluded; packaging not yet counted.',
      'Authoritative artifacts inspected':len(audit['sources'])+2,'artifact_count_scope':'16 directly inspected official/original sources, one immutable price database, one original author workbook; excludes derived files',
      'Unresolved blockers':[{'item':'Point-in-time news and expectations not acquired','blocks':'news factor validation'},{'item':'BW release-time/revision and historical fitting audit incomplete','blocks':'PIT sentiment timing claims'},{'item':'AAII original spreadsheet access403','blocks':'AAII reproduction'},{'item':'Seven SOX candidate gaps and unverified full calendar','blocks':'complete long-history coverage claims'},{'item':'No independent external audit or pristine future holdout','blocks':'independent replication/general efficacy claims'}],
      'Claims authorized':'Scoped deterministic price-state descriptions, retrospective rule-defined turns, directly inspected source facts, workbook schema audit',
      'Claims not authorized':'Sentiment predictive efficacy, causal market attribution, actual strategy excess return, universal turning-time rule',
      'Worktree/user data preserved':'YES','price_input_unchanged':True,'independent_validation':False,
      'self_check':selfcheck,'report_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'report_command':sys.argv}
    (ROOT/'AUDIT_FINAL.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
    print(json.dumps({'report':'Phase1_Research_Report_zh.md','status':'PARTIALLY_SUPPORTED','figures':2,'verified_sources':len(audit['sources']),'self_check':'PASS'}))

if __name__=='__main__':main()
