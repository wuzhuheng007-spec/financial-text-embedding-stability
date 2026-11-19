# 给Claude Code的执行指令

请直接执行下面的复核任务，保留所有失败和负面结果，不要只提供计划。

## 任务与禁止混淆的范围

复核仓库wuzhuheng007-spec/financial-text-embedding-stability中research/market_sentiment/phase5_strategy_v1。三项真实可运行候选是P1_TREND、P2_RECOVERY、P3_RISK_BUDGET，比较BUY_HOLD和MA200；它们只用moomoo价格，是行情代理，不是已验证新闻情绪。T1/T2在没有真实历史文本/事件时必须继续标为BLOCKED。

原始预运行锁定提交：1cfe1acb9e82c5c22f2ec1122c41cc2dd1209953。先取得该提交的config.json、run.py、check.py、PROTOCOL_zh.md并核对当前副本哈希与首轮results/run_manifest.json；研究结果提交比锁定提交晚是正常的。不要把分支最新代码或其他平台的momentum20基线混进本实验。

先读PROTOCOL_zh.md、config.json、RESULTS_zh.md、results/run_manifest.json、results/self_check.json及本目录的AUDIT_FINAL.json。保留原文件，所有复核写入新目录claude_validation，禁止覆盖首轮结果、改历史价格、删负面结果、部署或下真实订单。

## A：同输入复现

1. 在用户当前项目及已知数据导出目录中定位market_research.sqlite。读取SHA-256、表/视图和日期范围。首轮输入SHA-256为c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e。不要用同名文件当成同版本；多个候选先列库存。
2. 如果本地缺完全相同的库，明确记录EXACT_INPUT_NOT_AVAILABLE，不伪造A通过，继续执行B中能完成的数据核对及C。数据库连接只读；不要启动OpenD交易功能。
3. 记录Python、NumPy、Pandas、SciPy版本。首轮环境见environment.json。先用现有兼容环境；如要建立隔离环境，推荐Python3.12并采用requirements.txt中的首轮版本。环境不同仍可运行，但不能承诺字节一致。
4. 在本目录执行以下命令；将DB_PATH替换为实际查到的绝对路径，不要假定它在GPT的工作目录。输出目录必须全新：

```bash
python check.py --unit
python run.py --db "DB_PATH" --private-out "claude_validation/exact_private" --public-out "claude_validation/exact_public" --freeze-commit 1cfe1acb9e82c5c22f2ec1122c41cc2dd1209953 --mode exact
python check.py --db "DB_PATH" --private-out "claude_validation/exact_private" --public-out "claude_validation/exact_public"
```

5. 对照首轮metrics.csv、contrasts.csv、factor_diagnostics.csv、private_output_hashes.json。首先比较策略信号/执行日期/区间数及原始CSV哈希，再比较指标。运行时间和本地路径不要求一致；记录所有数值差异，不改结果让它们相等。只重复运行同一程序是可重复性检查，不叫独立实现或新市场样本。

## B：独立实现与账本核对

6. 在新文件independent_reference.py中，依据PROTOCOL/config从原始日线重新实现特征、状态机、次日开盘执行和现金/单位账本。不得调用run.features、run.simulate、run.rebalance来生成独立结果，也不得直接加载首轮信号作为输入。
7. 必须核对：252条暖机；SOX缺口切片及后验边界；P2压力记忆、40条持仓和按收盘最高值的止损；P3目标权重与持仓漂移；扣费后目标资产值方程；首尾费用；跨时期重新起始现金；同一日期集合上的MA200对照。
8. 用独立小例子检查现金守恒和成本；对每个市场做至少两个截断前缀和一次未来价格扰动，已过去的信号不得改变。抽样核对之外还要全量比较目标权重、交易日期、净收益和NAV；可参考相对/绝对容差1e-10与1e-8，但必须报告实际最大误差。
9. 独立复算全部主评价配对日收益差、循环区块不确定性和12项Holm，保留块长63/126/252和两seed。8自然周因子相关是各市场时序描述，不能当四市场横截面IC或独立样本t检验。

## C：用户另一份数据上的外部检查

10. 对用户自己的新导出数据另建input_inventory.json：文件哈希、字段映射、代码、复权、时区、起止和缺失日期；明确是否只是同一供应商同一批数据、不同下载版本、增加历史还是新增未来日期。相同历史复跑不构成新的行情复制。
11. 若数据不是首轮SQLite，写可审查的只读适配脚本，将其转为具有series、observations、research_daily、unresolved_dates接口的派生研究库；保留原始值和行级来源。不要用今天的成分回填、用未核验成交量、前向填补缺失价格或把零值当真。
12. 复权不明确时，不擅自改用另一ETF/指数或总回报序列。先登记口径差异；用重叠日期逐条比较OHLC、排除规则和日期集合。科创50仍从实际可发布日起使用。若证券或字段不能唯一对应，只将相应标的标为BLOCKED，不拿替代输入冒称精确复现。
13. 原参考程序要求四个标的及足够历史均可用，才执行整套external模式，固定相同策略参数。若仅有部分标的或没有主评价区间，明确标记整套参考复跑BLOCKED；可在独立实现中按可用标的另建external_partial协议、配置及结果，但不能修改冻结原件或把部分家族当原12项验收。完整输入执行：

```bash
python run.py --db "NEW_DERIVED_DB_PATH" --private-out "claude_validation/external_private" --public-out "claude_validation/external_public" --freeze-commit 1cfe1acb9e82c5c22f2ec1122c41cc2dd1209953 --mode external --end "NEW_VERIFIED_END_DATE"
python check.py --db "NEW_DERIVED_DB_PATH" --private-out "claude_validation/external_private" --public-out "claude_validation/external_public"
```

external模式只放开输入身份及截止日，不改变策略阈值；代码中primary仍为2015—2025。新增晚于首轮2026-11-17的日期进入full/monitor，但不能把这些结果与历史primary混算。若要对真正新增日期单列前瞻评价，新增独立协议/脚本，保留原结果，声明谁何时看过这些日期。若不是未见数据，就标“外部版本/实现检查”。

## D：返回给GPT的结果

14. 输出VALIDATION_zh.md和VALIDATION.json，逐项对应A/B/C，分别给出同输入复现、独立实现、另一数据版本/时期的状态。不要用一个“通过”替代三项。
15. 至少包含：真实运行命令、开始结束时间、代码/配置/输入哈希、环境、全部策略/基准/费用/时期指标、主检验及未通过结果、最大账本误差、缺失/差异、是否具备真实新闻数据、与首轮逐项对账表。
16. 若没有候选通过预设门槛，就明确写没有通过；不得删标的、换基准、翻转因子方向、改阈值后仍称原方案通过。想优化可写新的探索提议，但本任务先完成固定方案复核。
17. 将独立代码、结构化结果、运行日志、账本哈希及报告打包回传。未经分享权核验，原始商业行情、逐行账本和凭据不上传公开GitHub；公开仅保留协议、代码及合规汇总。本任务不启动自动实盘。
