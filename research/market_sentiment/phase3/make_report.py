"""Generate aggregate figures, every-event atlas, and traceable Chinese report."""
from pathlib import Path
import datetime,hashlib,json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

R=Path(__file__).resolve().parent;O=R/'results_v1_1';P=R/'private_v1_1'
NAMES={'US.QQQ':'QQQ','SH.000001':'上证综指','SH.000688':'科创50','US..SOX':'SOX'}
ENG={'US.QQQ':'QQQ','SH.000001':'Shanghai Composite','SH.000688':'STAR 50','US..SOX':'SOX'}
def sh(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def num(x,n=3):return '缺失' if pd.isna(x) else f'{x:.{n}f}'
def pct(x):return '缺失' if pd.isna(x) else f'{100*x:.2f}%'
def mdtable(rows,headers):return '\n'.join(['|'+'|'.join(headers)+'|','|'+'|'.join(['---']*len(headers))+'|']+['|'+'|'.join(str(v).replace('|',' / ') for v in row)+'|' for row in rows])

def aggregates(ev,panels):
    raw=[];same=[]
    for sym,a in panels.items():
        for per in ['all','development','check','late_check','monitor']:
            b=a if per=='all' else a.loc[a.period.eq(per)]
            for proxy in ['vix_change20','credit_change20','rate_change20']:
                q=b[[proxy,'return20']].dropna()
                same.append({'symbol':sym,'proxy':proxy,'period':per,'n':len(q),'spearman_overlapping_20_observation_windows':float(spearmanr(q[proxy],q.return20).statistic) if len(q)>2 else np.nan,'prediction_claim':False})
    pd.DataFrame(same).to_csv(O/'overlapping_window_associations.csv',index=False)
    for _,e in ev.loc[ev.threshold.eq(.2)].iterrows():
        a=panels[e.symbol];i=a.index.get_loc(pd.Timestamp(e.origin_extreme_date_hindsight));base=a.close.iloc[i];risk=a.risk_appetite.iloc[i]
        for tau in range(-60,61):
            j=i+tau
            if j<0 or j>=len(a) or a.segment.iloc[j]!=e.segment:continue
            raw.append({'symbol':e.symbol,'direction':e.direction,'event_id':e.event_id,'tau':tau,'relative_price':a.close.iloc[j]/base-1,'risk_change_points':a.risk_appetite.iloc[j]-risk})
    frame=pd.DataFrame(raw);rows=[]
    for key,b in frame.groupby(['symbol','direction','tau']):
        rows.append(dict(zip(['symbol','direction','tau'],key))|{'n_price':int(b.relative_price.notna().sum()),'n_risk':int(b.risk_change_points.notna().sum()),'price_median':b.relative_price.median(),'price_q25':b.relative_price.quantile(.25),'price_q75':b.relative_price.quantile(.75),'risk_median':b.risk_change_points.median(),'risk_q25':b.risk_change_points.quantile(.25),'risk_q75':b.risk_change_points.quantile(.75)})
    out=pd.DataFrame(rows);out.to_csv(O/'event_aligned_medians.csv',index=False)
    fig,axes=plt.subplots(2,4,figsize=(16,7),sharex=True)
    for j,sym in enumerate(NAMES):
        for direction,color,label in [('UP','#007d6c','Up-leg origin'),('DOWN','#b33a3a','Down-leg origin')]:
            b=out.loc[out.symbol.eq(sym)&out.direction.eq(direction)].sort_values('tau')
            axes[0,j].plot(b.tau,b.price_median*100,color=color,label=label)
            axes[1,j].plot(b.tau,b.risk_median,color=color,label=label)
        axes[0,j].set_title(ENG[sym]);axes[0,j].set_ylabel('Median price vs origin (%)');axes[1,j].set_ylabel('Median risk-appetite change (points)');axes[1,j].set_xlabel('Local quote sessions vs hindsight origin')
        for ax in axes[:,j]:ax.axvline(0,color='#888888',lw=.8);ax.axhline(0,color='#888888',lw=.8);ax.grid(alpha=.2)
    axes[0,0].legend(fontsize=8);fig.suptitle('20% directional-change atlas | hindsight alignment, current vintage | descriptive only',fontsize=12)
    fig.tight_layout();fig.savefig(O/'event_aligned_medians.svg');fig.savefig(P/'event_aligned_medians.png',dpi=130);plt.close(fig)
    return pd.DataFrame(same)

def main():
    check=json.loads((O/'self_check.json').read_text());assert check['status']=='PASS_SAME_AUTHOR_SELF_CHECK'
    ev=pd.read_csv(O/'events_all_thresholds.csv');primary=ev.loc[ev.threshold.eq(.2)];panels={}
    for sym in NAMES:
        panels[sym]=pd.read_csv(P/(sym.replace('.','_')+'_panel.csv'),parse_dates=['date']).set_index('date')
    same=aggregates(ev,panels)
    assoc=pd.read_csv(O/'associations.csv');model=pd.read_csv(O/'model_contrasts.csv');alerts=pd.read_csv(O/'alert_summary.csv');coverage=pd.read_json(O/'coverage.json')
    # Exact displayed cells, not rounded figures as the numerical authority.
    display={
      'event_counts':primary.groupby(['symbol','direction']).size().rename('n').reset_index().to_dict('records'),
      'confirmation_lags':primary.groupby(['symbol','direction']).confirmation_lag_calendar_days.median().rename('median_calendar_days').reset_index().to_dict('records'),
      'primary_associations':assoc.loc[assoc.period.eq('all')&assoc.horizon_weeks.eq(8)].to_dict('records'),
      'primary_models':model.loc[model.period.eq('all')&model.horizon_weeks.eq(8)&model.block_sessions.eq(126)].to_dict('records'),
      'late_check_joint_alerts':alerts.loc[alerts.period.eq('late_check')&alerts.horizon_weeks.eq(8)&alerts.barrier.eq(.1)&alerts.rule.isin(['joint_up','joint_down'])].to_dict('records'),
      'overlapping_20_day_associations':same.loc[same.period.eq('all')].to_dict('records'),
    }
    # Convert missing numbers to JSON null, not non-standard NaN.
    def clean(value):
        if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
        if isinstance(value,list):return [clean(v) for v in value]
        if isinstance(value,float) and not np.isfinite(value):return None
        return value
    (O/'display_cells.json').write_text(json.dumps(clean(display),ensure_ascii=False,indent=2,allow_nan=False))
    policy=pd.read_csv(R/'results/official_calendar/statement_links.csv');policy['d']=pd.to_datetime(policy.date_from_url)
    atlas=['# 全部20%行情事件逐条索引','', '这些是固定方向变化算法的全部输出，不代表所有可能的行情定义。最低/最高日是事后极值；确认日才是阈值满足日。已结束与尚未结束的腿均列出。SOX缺口前后的腿分开；0表示缺少相应信号，不能补造原因。','', '窗口为起点前/后20个本地报价日，同一价格片段。候选信号见协议；每次具体阈值、风险分数和完整日期字段见CSV。Fed链接仅为起点前后20自然日的官方档案线索；中国市场同样只提供外部政策背景，不是本土原因或新闻全集。','']
    policy_links=[]
    for sym in NAMES:
        atlas+=['## '+NAMES[sym],'']
        for _,e in primary.loc[primary.symbol.eq(sym)].iterrows():
            d=pd.Timestamp(e.origin_extreme_date_hindsight);near=policy.loc[policy.d.between(d-pd.Timedelta(days=20),d+pd.Timedelta(days=20))].sort_values('d')
            sig=[]
            for rule,label in [('price_up','价格转强'),('price_down','价格转弱'),('risk_easing','风险缓解'),('risk_break','风险破裂'),('joint_up','联合转强'),('joint_down','联合转弱')]:
                before=e[rule+'_pre20_onsets'];after=e[rule+'_post20_onsets']
                if pd.notna(before):sig.append(label+'：前 '+str(before).replace('|','、'))
                if pd.notna(after):sig.append(label+'：后 '+str(after).replace('|','、'))
            links=['['+str(x.date_from_url)+']('+x.statement_url+')' for _,x in near.iterrows()]
            for _,x in near.iterrows():policy_links.append({'event_id':e.event_id,'statement_url':x.statement_url,'url_date':x.date_from_url,'calendar_days_from_hindsight_origin':int((x.d-d).days),'role':'UNVERIFIED_CAUSALITY_DATE_PROXIMITY_ONLY','statement_fulltext_this_phase':'NOT_FETCHED'})
            atlas+=['### '+e.event_id+' · '+('上行' if e.direction=='UP' else '下行'),'', '事后起点 '+e.origin_extreme_date_hindsight+'；20%确认 '+e.threshold_confirmation_date+'；极值终点 '+e.end_extreme_date_hindsight+'；'+('已由下一次反向20%确认结束' if e.complete else '右删失/后续未确认结束')+'。确认延迟 '+str(e.confirmation_lag_calendar_days)+' 自然日；起点前一报价日风险偏好候选分数 '+num(e.origin_pre_risk_appetite,1)+'。','', '观察到的候选信号：'+('；'.join(sig) if sig else '窗口内无合格候选信号')+'。','', '同期Fed档案：'+('、'.join(links) if links else '指定窗口内未匹配到本索引声明')+'。','', '归因：未建立因果解释；A股本土政策/融资/新闻证据待补充。','']
    (R/'ALL_EVENTS_20pct_zh.md').write_text('\n'.join(atlas));pd.DataFrame(policy_links).to_csv(O/'event_policy_date_links.csv',index=False)
    counts=[]
    for sym in NAMES:
        p=primary.loc[primary.symbol.eq(sym)];cov=coverage.loc[coverage.symbol.eq(sym)].iloc[0]
        counts.append([NAMES[sym],cov['first'],cov['last'],int(p.direction.eq('UP').sum()),int(p.direction.eq('DOWN').sum()),int(p.complete.sum()),int((~p.complete).sum()),int(p.midterm_confirmation.sum())])
    arows=[]
    for sym in NAMES:
        row=assoc.loc[assoc.symbol.eq(sym)&assoc.proxy.eq('risk_appetite')&assoc.horizon_weeks.eq(8)&assoc.period.eq('all')].iloc[0]
        arows.append([NAMES[sym],int(row.n),num(row.spearman),f'[{num(row.ci_low)}, {num(row.ci_high)}]',num(row.holm_adjusted_p)])
    mrows=[]
    for sym in NAMES:
        values=[]
        for variant in ['price_vix','price_funding','price_both']:
            row=model.loc[model.symbol.eq(sym)&model.horizon_weeks.eq(8)&model.period.eq('all')&model.block_sessions.eq(126)&model.model.eq(variant)].iloc[0]
            values.append(pct(row.relative_MSE_change))
        mrows.append([NAMES[sym],int(row.n),*values])
    joint=[]
    for sym in NAMES:
        for rule in ['joint_up','joint_down']:
            x=alerts.loc[alerts.symbol.eq(sym)&alerts.horizon_weeks.eq(8)&alerts.period.eq('late_check')&alerts.barrier.eq(.1)&alerts.rule.eq(rule)].iloc[0]
            joint.append([NAMES[sym],('联合转强' if rule=='joint_up' else '联合转弱'),int(x.mature),int(x.desired_first),int(x.opposite_first),int(x.no_barrier),pct(x.mean_forward_return)])
    lagrows=[]
    for sym in NAMES:
        q=primary.loc[primary.symbol.eq(sym)]
        lagrows.append([NAMES[sym],num(q.loc[q.direction.eq('UP'),'confirmation_lag_calendar_days'].median(),1),num(q.loc[q.direction.eq('DOWN'),'confirmation_lag_calendar_days'].median(),1)])
    contempor=[]
    for sym in NAMES:
        x=same.loc[same.symbol.eq(sym)&same.proxy.eq('vix_change20')&same.period.eq('all')].iloc[0]
        contempor.append([NAMES[sym],int(x.n),num(x.spearman_overlapping_20_observation_windows)])
    summary=json.loads((O/'run_manifest.json').read_text());official=json.loads((R/'results/official_calendar/summary.json').read_text())
    text=f'''# 第三阶段研究报告：行情启动、风险情绪与反转候选

截至2026-11-19；运行 `{summary['run_id']}`。结论来自冻结协议及明确登记的边界修正，已作同作者原始输入自查，没有独立复制。

## 先回答研究问题

已经完成四条可用日线历史的全样本扫描，而非只挑著名牛熊市。20%口径共 **{len(primary)}** 段方向已确认行情，其中 **{int(primary.complete.sum())}** 段的终点已由下一次反向阈值确认，**{int((~primary.complete).sum())}** 段右删失；10%口径 **{int(ev.threshold.eq(.1).sum())}** 段。每段都有事后起点、可观察确认日、前后候选信号、风险偏好分数、缺口与归因状态。[逐条事件卡](ALL_EVENTS_20pct_zh.md)与[全部两个阈值CSV](results_v1_1/events_all_thresholds.csv)保留所有事件。

最重要的发现是：**短窗风险价格与股票有明显同步关联，并不自动意味着能预测中期收益。** 本轮没有确立跨市场可靠的反转标准或增量预测因子。QQQ与SOX风险偏好水平对未来8周的秩相关偏负，但家族内多重比较调整后不足以作可靠预测结论；加入VIX/融资变量也未在主比较中通过该检验。

## 覆盖与“每次”的边界

{mdtable(counts,['市场','开始','结束','上行腿','下行腿','完整腿','右删失腿','182日内确认'])}

“每次”严格指冻结方向变化算法在已验收数据内的全部输出，不能等同所有投资者主观认定的上涨和下跌。QQQ与科创50上市/发布前无数据；SOX已知7个缺失交易日期拆为5段，跨段特征与标签禁止连接。上证综指、QQQ、科创50、半导体板块代表的市场不同，尤其后两类科技暴露不能替代全市场广度。本轮没填补缺口、没制造科创50早期历史。

## 起点与确认日不能混同

{mdtable(lagrows,['市场','上行20%确认延迟中位数/自然日','下行20%确认延迟中位数/自然日'])}

这些延迟是算法的事后描述，不是预言拐点所需的固定等待天数。20%确认可用于一致地标注历史；若以确认日才观察，行情已经走过一段，后面仍可能失败。因此没有把最低点当作当时已知的买入时机。

候选早期标准固定为：价格站上60日均线且20日收益为正；价格跌破60日均线且20日收益为负；VIX过去21个来源观测曾不低于30、且自高点回落20%；或VIX自不高于15的21观测低点抬升50%。联合条件也评估，假转真才发信号，20本地报价日冷却。阈值都是本研究预先固定的操作定义，不能称公认有效的“标准信号”。

## 如何把情绪量化

候选风险偏好分数为 `100 × (1 − VIX过去1260个来源有效观测的百分位)`，至少756个来源观测，含当期且平均处理并列。高分表示相对过去约5年美国期权风险定价较低，低分反之；它不是投资者心理的直接测量，也没有本轮验证成一个成熟因子。VIX本身衡量期权隐含近期波动预期，[FRED说明](https://fred.stlouisfed.org/series/VIXCLS)支持该定义。

另考察VIX的20观测比例变化、Baa与10年美债利差及其20观测绝对变化、10年美债收益率的20观测变化。[BAA10Y官方定义](https://fred.stlouisfed.org/series/BAA10Y)是Baa减10年美债，并非高收益债OAS；[DGS10](https://fred.stlouisfed.org/series/DGS10)是10年恒定期限美债收益率。用于中国市场时都只是外部美国风险环境，不能取代中国融资杠杆、新增开户、基金申赎、全市场涨跌广度与政策预期。

为防止同日时区与发布混淆，每个本地日期只取严格早于该日的第二近美国有效来源日期，最长陈旧7自然日。仍为当前修订版本，不能声称已恢复历史时点可得信息。[Cboe历史说明](https://www.cboe.com/tradable-products/vix/vix-historical-data/)及Andersen、Bondarenko、Gonzalez-Perez的[2024工作论文](https://cdn.cboe.com/resources/education/research_publications/VIXInterpolationWhitepaper.pdf)显示现代VIX在2003年改变方法并回算至1990，2014年又纳入周到期期权且未对旧历史作同口径回填。这是长历史可比性的额外限制，本轮未消除方法断点。

## 与股价的同步关系和预测关系

先看20观测变化窗口的重叠关联。由于美国代理滞后对齐、本地交易日历不同，它不是精确同刻的因果反应；绝不能称领先20天预测。

{mdtable(contempor,['市场','完整重叠窗口数','VIX变化 vs 20日本地收益 Spearman'])}

再看严格向前的8自然周结果，从下一本地收盘到期限末收盘。以下为滞后风险偏好**水平**与未来收益，不是把发布指数直接差分；CIs是126个合格日行块、999次自助法的逐项区间。16项主关联同时报告Holm调整p值。

{mdtable(arows,['市场','有效日窗口数','Spearman','95%区间','Holm调整p'])}

区间未作同时覆盖调整，须结合Holm列阅读。日窗口重叠、市场相关，不能将日窗口数当成独立危机数。完整4/8/12周及分期结果见[associations.csv](results_v1_1/associations.csv)。低风险偏好水平可能出现在抛售后，使样本里的未来收益偏高；这也不是证明其可提前识别底部或构成因果机制。

## 超过单看价格，是否多提供信息

固定Ridge逐年扩展训练，测试年2011—2026，至少756条训练样本。训练标签必须在测试年开始前成熟，标准化只用训练窗。同一比较里的价格、价格+VIX、价格+融资、两者均用完全相同的合格行；另保留训练均值基线。参数未按本轮测试成绩调优，但当前历史已被研究过，不能称全新盲测。

下表为未来8周**预测均方误差相对价格模型变化**，负数较好、正数较差，不是收益率。所有测试年合并，含单列保存的2026监测期。

{mdtable(mrows,['市场','预测日窗口数','加VIX','加融资环境','两者都加'])}

主12项市场×增加变量比较在126日块下，没有Holm调整p低于0.05的改善。科创50样本短，区间对块长度敏感，独立行情数更少，不能把单项看似改善解释成稳健有效。不同市场的拟合年数不同，见[model_fit_coverage.csv](results_v1_1/model_fit_coverage.csv)。全部变体、三期限、分期、63/126/252日块、MAE与方向准确率均保留，见[model_contrasts.csv](results_v1_1/model_contrasts.csv)和[model_summary.csv](results_v1_1/model_summary.csv)。只试一个固定模型族，本结论不排除其他结构或未来更好数据的作用。

## 早期联合信号会不会失效

下表只展示2017—2025后期检查段（科创50自有效历史开始），信号后下一收盘起算8周：同向先到±10%收盘障碍、反向先到、均未到，三类互斥。未到不是自动成功，也不是自动失败。期末平均涨跌并非策略收益。

{mdtable(joint,['市场','信号','成熟信号数','同向先到','反向先到','均未到','期末平均涨跌'])}

结果不足以把“恐慌回落+价格转强”当作统一反转标准。单独VIX缓解、单独价格信号、其他期限/障碍及右删失均在[alert_summary.csv](results_v1_1/alert_summary.csv)。表里的价格状态均值只是参考队列；纯风险信号自身不要求价格状态，不能把该参考队列冒称严格匹配的因果对照。增量比较应主要看前面的同样本模型诊断。

## 情绪变化轨迹

![全部20%事件对齐后的价格与风险偏好中位轨迹](results_v1_1/event_aligned_medians.svg)

横轴为事后极值起点前后60个本地报价日。上排为价格相对起点变化，下排为风险偏好候选分数相对起点变化，中位数按市场/方向逐点计算。每点样本数及四分位数在[event_aligned_medians.csv](results_v1_1/event_aligned_medians.csv)。选择极值本身会机械地产生价格V形/倒V形；图只能帮助观察风险代理路径，不能当作预测证据。各点可用样本不同、事件可重叠，图中线不是置信区间，也不是独立样本平均投资收益。

## 政策与新闻时间线完成到了哪里

实际获取1996—2020全部25个年度档案和2021—2026当前日历，共 **{official['successful_indexes']}** 个官方索引，提取 **{official['unique_statement_urls']}** 条不重复声明URL。[年份覆盖](results/official_calendar/year_coverage.csv)与[全部声明链接](results/official_calendar/statement_links.csv)可逐条核对。日期来自URL，正文未在本阶段逐条读取，全文首次公开时刻、市场预期与意外程度均未建立。[Fed官方日历](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)明确会议声明与之后发布的会议纪要不同，不能把纪要内容回填到会议当天。

每次事件卡已连接起点前后20自然日的声明URL，只有日期邻近意义。90年代并非每次不变息会议都发声明，档案也不覆盖所有非例会政策；索引条数不能解释为政策动作次数。中国30年完整政策/新闻与融资情绪资料仍未完成，不能声称已解释了每轮涨跌原因。未核实的当日新闻例子不进入原因库。

## 自查、修正与可复现性

首轮边界自查发现6处跨SOX片段注释、8个前态未知的起始信号；修正先登记[AMENDMENT_v1_1.json](AMENDMENT_v1_1.json)，原结果和原代码保留。新版本只允许已观察的假→真状态且注释不跨段，重跑后价格模型和关联表与首轮一致。

已从原SQLite逐行核对价格、特征、未来收益，核对美国代理来源日期、阈值首次达到与极值、信号冷却、先触障碍，并以增广最小二乘重拟合全部保存Ridge参数；执行未来价格扰动/对齐前缀检查。详见[self_check.json](results_v1_1/self_check.json)。这是同作者自查，不是独立验证。

每个展示数字映射至[display_cells.json](results_v1_1/display_cells.json)、原结果CSV、[run_manifest.json](results_v1_1/run_manifest.json)、代码/冻结配置及输入SHA256。公开代码和事件/汇总表；逐日商业行情、原始风险数据、模型逐行预测及参数证据保持私有，不因代码公开而获得再分发权。

## 后续研究的具体目标

1. 补齐中国本土情绪变量的原始发布版本/制度变动：融资余额与杠杆、开户和基金流量、全市场历史成分广度；长度不足就做短样本模块，不能硬拼30年。
2. 扩充有首次发布时间、市场预期、新闻新颖度的真实事件面板，区分利好兑现与利空钝化；当前价格与风险状态作为强基线，避免把涨价重新包装为情绪。
3. 原始发布版本与独立复核具备后，另行冻结新窗口/新模型；先证明相对于价格基线有跨时期稳定增量，再讨论组合权重与交易实现。

研究结论状态：**PARTIALLY_SUPPORTED**。事件库及限定输入诊断已执行；跨市场可靠反转标准、完整情绪测量、新闻因果与可交易超额收益均未确立。
'''
    (R/'Phase3_Event_Atlas_Report_zh.md').write_text(text)
    # Machine-checkable evidence/plan reconciliation, distinct from human prose.
    ledger={'run_id':summary['run_id'],'claims':[{'id':'P3-EVENTS','claim':'All fixed-rule events in accepted histories enumerated','status':'VERIFIED_WITHIN_STATED_RULE_AND_COVERAGE','evidence':['results_v1_1/events_all_thresholds.csv','results_v1_1/open_segments.csv','results_v1_1/self_check.json']},{'id':'P3-PREDICT','claim':'Universal reversal or robust incremental predictive efficacy','status':'INCONCLUSIVE','evidence':['results_v1_1/associations.csv','results_v1_1/model_contrasts.csv','results_v1_1/alert_summary.csv']},{'id':'P3-NEWS','claim':'Complete causal news explanation for all historical turns','status':'NOT_VERIFIED','evidence':['results/official_calendar/summary.json']}],'plan_execution':[{'id':'P30','status':'PASS','evidence':['config.json','freeze_receipt.json','input_manifest.json']},{'id':'P31','status':'PASS_AFTER_DISCLOSED_IMPLEMENTATION_FIX','evidence':['results_v1_1/events_all_thresholds.csv','results_v1_1/alert_summary.csv','AMENDMENT_v1_1.json']},{'id':'P32','status':'PASS_EXPLORATORY_CURRENT_VINTAGE','evidence':['results_v1_1/model_summary.csv','results_v1_1/model_contrasts.csv']},{'id':'P33','status':'PASS_FOR_METADATA_UNIVERSE_ONLY','evidence':['results/official_calendar/index_fetch_inventory.csv','results/official_calendar/statement_links.csv']},{'id':'P34','status':'PASS_SAME_AUTHOR','evidence':['results_v1_1/self_check.json','Phase3_Event_Atlas_Report_zh.md']},{'id':'P35','status':'TO_BE_RECONCILED_IN_GIT_SYNC_RECEIPT','evidence':[]}],'numerical_chain':'display_cells -> result CSV -> raw/prediction hashes in run_manifest -> config/input hashes -> source files','limitations':['PIT unavailable','complete China/news expectations unavailable','not independently replicated','finite correlated market/one-model-family diagnostic'],'artifact_hashes':{str(p.relative_to(R)):sh(p) for p in [R/'config.json',R/'freeze_receipt.json',R/'AMENDMENT_v1_1.json',O/'run_manifest.json',O/'display_cells.json',O/'self_check.json',R/'Phase3_Event_Atlas_Report_zh.md']}}
    (R/'evidence_ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2))
    print(json.dumps({'report':str(R/'Phase3_Event_Atlas_Report_zh.md'),'primary_events':len(primary),'sensitivity_events':int(ev.threshold.eq(.1).sum()),'event_cards':len(primary),'model_primary_comparisons':len(display['primary_models']),'no_holm_p_below_005':bool(min(x['holm_adjusted_p'] for x in display['primary_models'])>=.05)}))

if __name__=='__main__':main()
