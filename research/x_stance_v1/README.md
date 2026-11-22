# X 关键词情绪研究（x_stance_v1）

按关键词主题采集 X 帖子，只做看多 / 看空 / 中性三类立场，按账号算占比，归纳样本里的主要观点，
并与之后 1、3、5、10、20、30 个交易日的市场表现对照。合同版本 `x-keyword-stance-v1-proposed-20261122`。

**现状（2026-11-22）：只有离线工程。** 没有采集过真实帖子，没有跑过模型，没有任何市场结论。
执行情况见 [PLAN_EXECUTION.md](PLAN_EXECUTION.md)。

## 不能从这里得出的说法

- 不是全 X 的比例。占比的分母是“取回样本里可识别的账号”。
- 不是预测，也不是策略收益。收盘对收盘的条件收益只是描述。
- 历史回填只包含现在还能取到的帖子，不证明当时能同样收到。
- 标签准确率没有经过人工验收之前，不引用任何准确率。
- QQQ 自身的收益不是因子残差收益。

## 目录

| 位置 | 内容 |
|---|---|
| `config/x_stance_v1_frozen_20261122.json` | 参数。null 表示还没定，程序不会用默认值顶替 |
| `scripts/x_stance_v1/config.py` | 读取与校验；两道门（付费采集、分类模型） |
| `scripts/x_stance_v1/cost.py` | 成本计算与预算保护（Decimal） |
| `scripts/x_stance_v1/provider.py` | 规范化字段、查询构造、本地导入、TwitterAPI.io 采集器 |
| `scripts/x_stance_v1/store.py` | 私有 SQLite 库（合同第 12 节的表） |
| `scripts/x_stance_v1/stance.py` | 主题匹配、三分类接口、严格 JSON 校验、推理缓存、人工抽样与混淆矩阵 |
| `scripts/x_stance_v1/sessions.py` | 交易所会话窗口与纽约自然日窗口 |
| `scripts/x_stance_v1/aggregate.py` | 按账号占比、主导状态、提交时间 |
| `scripts/x_stance_v1/narratives.py` | 主要观点（最多 5 条） |
| `scripts/x_stance_v1/episodes.py` | 情绪事件与删失 |
| `scripts/x_stance_v1/returns.py` | 六期限收益配对 |
| `scripts/x_stance_v1/stats.py` | 条件统计、区块自助法、留出期切分 |
| `scripts/x_stance_v1/portal_read.py` | 页面读取模型（只用标准库） |

## 常用命令（都不发网络请求）

```
python -m scripts.x_stance_v1.cli status
python -m scripts.x_stance_v1.cli cost-table
python -m scripts.x_stance_v1.cli plan --start 2026-01-05T00:00:00Z --end 2026-01-06T00:00:00Z
python -m scripts.x_stance_v1.cli import-local 文件.jsonl
```

`acquire` 是唯一会联网的命令。它在以下任一条件不满足时拒绝并退出：配置里 `dry_run=false` 且 `live_enabled=true`、
环境变量 `X_STANCE_TWITTERAPI_IO_KEY` 存在、每日预算和总预算已填、回填起止日期已填。密钥只从环境变量读取，不写入配置、日志或输出。

## 数据放在哪里

私有库默认在 `local_only/x_stance_v1/x_stance.sqlite3`（可用 `QUANT_X_STANCE_DB` 改）。帖子原文、供应商返回原件和行情快照都不进仓库。
本目录和测试里出现的帖子、标签、价格全部是编造的。

## 几个口径

- 交易日窗口：`[上一交易日实际收盘, 当日实际收盘)`。正好在收盘那一秒发的帖子算下一个交易日。周末和假日的帖子并入随后第一个交易日，只算一次。
- 日历只覆盖 2019-11-01 至 2026-02-27。范围外的时间标 `CALENDAR_NOT_COVERED`，不用“工作日”近似。
- 每个账号一票：窗口内最后一条有效、相关、已判定的帖子。有效账号数为 0 时占比为空，不是 0%，也不是中性。
- 主导状态：采集完整且账号数达到阈值时取最多的一类，并列记中性；否则是 UNKNOWN。UNKNOWN 在情绪事件里是缺口，不是中性。
- 历史回填没有真实的提交时间。可成交对照里的入场日是“假设窗口结束时就能用”，结果带 `RETROSPECTIVE_VISIBLE_NOW_HYPOTHETICAL` 标记。
