"""Render reported numbers directly from saved first-run outputs."""
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parent
p=ROOT/'results';m=pd.read_csv(p/'metrics.csv');c=pd.read_csv(p/'contrasts.csv');r=json.loads((p/'run_manifest.json').read_text());v=json.loads((p/'self_check.json').read_text())
a=m[(m.period=='primary')&(m.cost_bps==10)].copy();tests=c[c.primary_test.astype(bool)].copy()
names={'US.QQQ':'QQQ','US..SOX':'SOX','SH.000001':'上证综指','SH.000688':'科创50'}
strategies=['BUY_HOLD','MA200','P1_TREND','P2_RECOVERY','P3_RISK_BUDGET']
cells=[];rows=[]
for symbol in names:
 sub=a[a.symbol==symbol];nums=[]
 for strategy in strategies:
  z=sub[sub.strategy==strategy].iloc[0];nums.append(f'{z.cagr*100:.2f}%')
  cells.append({'symbol':symbol,'strategy':strategy,'period':'primary','cost_bps':10,'field':'cagr','raw_value':float(z.cagr),'display':nums[-1],'source':'results/metrics.csv','run_manifest':'results/run_manifest.json'})
 rows.append('|'+names[symbol]+'|'+'|'.join(nums)+'|')
ddrows=[]
for symbol in names:
 sub=a[a.symbol==symbol];b=sub[sub.strategy=='BUY_HOLD'].iloc[0];z=sub[sub.strategy=='P3_RISK_BUDGET'].iloc[0]
 ddrows.append(f'|{names[symbol]}|{b.max_drawdown*100:.2f}%|{z.max_drawdown*100:.2f}%|{z.mean_exposure*100:.2f}%|')
text='''# 三项行情策略：首轮实跑结果与Claude交接

结论：固定规则已实际运行并完成同作者账本复核；预设收益增量门槛没有候选通过。当前结果不能支持实盘因子准入。P3部分风险指标改善属于较低市场暴露下的描述性结果，不等于收益alpha。

协议与程序在真实行情计算前提交并读回，冻结提交：FREEZE。运行开始（北京时间）：START；运行结束（北京时间）：END。当前是看过历史之后的新规则探索，不是未见预注册。

## 固定策略

P1为风险标准化60条动量、MA120入场/MA60退出的趋势规则；P2为压力后5条回升与MA20确认的限期修复；P3在P1方向上加10%年化波动预算并限制最高100%仓位。精确阈值、暖机、状态机与执行见PROTOCOL_zh.md和config.json，未按本轮收益优化。

T1新闻均值×波动、T2利空承受力均BLOCKED；只有OHLC，缺真实文本、事件时点及预期反应，不拿价格或成交量冒充新闻。

## 主评价净价格CAGR

主期2015—2025、单边10bps假设成本、现金0收益；相同市场同一开盘区间，末端平仓收费。科创50因实际发布起点与252条暖机，可评价期从2021-08-04开始；不能说四市场共同覆盖完整主期。QQQ为当前a1价格序列，未经认证是分红再投资总回报；三个指数是理论价格单位，不能直接交易。

|市场|买入持有|MA200基准|P1趋势|P2修复|P3波动预算|
|---|---:|---:|---:|---:|---:|
RETROWS

以上CAGR不是相对MA200的配对均值差。主显著性检验是日净收益差的区块抽样；不能混用两种指标。全部费用情景、时期和描述性Sharpe等保存在metrics.csv，不只保留主表。

## 风险与暴露的对照

|市场|买入持有最大回撤|P3最大回撤|P3平均资产暴露|
|---|---:|---:|---:|
DDROWS

风险改善没有自动控制市场暴露差异，不据此证明P3优于同风险基准。零现金利息与假设费用也限制可交易解释。

## 预设收益增量检验

12项市场×候选主检验，FIRSTPASS项通过。主比较为相对MA200的配对日净收益，循环块126、1999次、固定seed；块63/252与第二seed全部保留。门槛是均值>0、95%区块区间下界>0、12项Holm<=0.05且25bps仍有正均值差。完整值在contrasts.csv；不得只用原始p值或某个较好市场改判通过。

美股两个序列中三候选的主期价格CAGR均低于MA200；中国结果较为混合。科创50部分描述性收益改善但短样本区间包含零，没有通过收益增量门槛；不能据此推广为A股有效。

## 数量、复核与完整性

- 汇总ROWS行，包括没有足够区间的时期状态；真正生成LEDGERS份逐日账本，不称为ROWS个独立实验。
- 独立的现金/单位方程实现复核LEDGERS份保存账本，成本、NAV与CAGR/回撤对账通过；实际行情PREFIX次截断前缀检查通过。
- 这是同作者检查，不是Claude或第三方独立验证。因子8自然周相关只作各市场时序描述，重叠标签未作IID显著性推断。
- 输入DB、冻结配置、代码与汇总哈希在run_manifest.json；逐行账本/压缩哈希在private_output_hashes.json。报告数值显示保留两位百分比，未舍入源值在display_cells.json。
- full时期跨SOX已知缺口以独立片段评价；片段末端平仓是后验评价边界，不能冒称可预测缺口的交易信号。主期不含这些已列缺口；完整交易所日历仍未认证。

## 交接

CLAUDE_CODE_TASK.md要求分三层：同输入重复运行；依据协议独立实现；对用户另一数据版本/新增时期进行单列检查。相同供应商相同历史的复跑不能增加独立市场证据。原始输入及逐行账本只放私有交接包，不公开上传。

当前状态：工程执行与同作者对账完成；收益增量尚未通过；真实新闻因子未运行；独立验证待Claude返回。不要启动实盘，也不在此轮暗改阈值。
'''
for old,new in {'FREEZE':r['freeze_commit'],'START':pd.Timestamp(r['started_at_utc']).tz_convert('Asia/Shanghai').isoformat(),'END':pd.Timestamp(r['finished_at_utc']).tz_convert('Asia/Shanghai').isoformat(),'RETROWS':'\n'.join(rows),'DDROWS':'\n'.join(ddrows),'FIRSTPASS':str(r['primary_pass_count']),'ROWS':str(r['simulation_summary_rows']),'LEDGERS':str(r['private_ledger_files']),'PREFIX':str(v['real_run']['real_prefix_cases'])}.items():text=text.replace(old,new)
(ROOT/'RESULTS_zh.md').write_text(text,encoding='utf-8')
(p/'display_cells.json').write_text(json.dumps(cells,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'reported_cagr_cells':len(cells),'primary_passes':r['primary_pass_count']}))
