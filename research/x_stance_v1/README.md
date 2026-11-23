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

## R3（2026-11-23）新增的口径

- **样本版本 = 词表版本 + 账号规则版本。** 它是 `x_post_topics` 的键，也是每条序列和每个快照证据集的键。一条帖子只有是该版本的成员，才会进入这个版本的占比、观点、情绪事件和收益行；它在别的版本下留下的标签不会被带过来。每个日期按当时生效的词表派生，历史回填范围内的日期按回填计划冻结的词表派生。不同样本版本各成一条序列，不相接。
- **推理身份。** 标签按完整推理身份匹配：模型、固定版本（含权重文件哈希）、提示词、输出结构、全部生成参数、对话模板、引擎版本、适配器版本。任何一项变化都是新的身份，需要自己的推理；相同身份才复用缓存。早期没有完整身份的标签标为 `LEGACY_INFERENCE_IDENTITY`，保留原样，不计入当前配置。
- **三个时间。** `inference_started_at_utc` 在调用前读取，`inference_completed_at_utc` 在结果返回并通过校验之后读取，`label_written_at_utc` 是写入时刻。命中缓存时沿用原来的完成时间，只有写入时间是新的。信号的可用时间不早于材料接收、所用推理完成和快照首发三者。
- **两个分类队列。** 每日批量采到的帖子进近期队列（最新的采样日优先），历史回填进历史队列（最早的日期优先）；同一批内按主题、标的、帖子编号的哈希排序，不看语言、文本、账号大小或标签。近期队列有待处理项时历史队列不工作。
- **账号规则（所有者 2026-11-23 的指示）。** 点赞数低于下限的帖子不进样本，也不送去分类；账号的一票按粉丝数分档或指定账号加权。两个数字都是帖子被采到那一刻供应商给出的值。加权占比是主口径，等权占比（每个账号一票）始终并列给出；样本过少的判断仍然看账号个数，不看权重之和。
- **服务器以外的分类。** `export-pending` 导出带哈希的待分类文件，本机 GPU 上的 `local_gpu work` 产出结果，`import-results` 逐条核对帖子、文本、样本成员和推理身份后入库，重复导入不会多写。文件里有帖子正文，只走现有 SSH 通道，不进仓库。

## R3 新增命令

| 命令 | 作用 |
|---|---|
| `python -m scripts.x_stance_v1.cli queue` | 两个队列的积压、最早待处理材料的等待时间、各采样日完成率 |
| `python -m scripts.x_stance_v1.cli classify --queue recent --limit N --max-seconds S` | 只处理近期队列，带调用次数和时间上限 |
| `python -m scripts.x_stance_v1.cli export-pending FILE` / `import-results FILE` | 交给本机 GPU 的待处理文件与结果回传 |
| `python -m scripts.x_stance_v1.local_gpu inventory --root DIR` | 读取本机显卡、库版本和已缓存的模型 |
| `python -m scripts.x_stance_v1.local_gpu bench --model DIR --materials FILE --out FILE` | 在开发材料上做工程诊断（不是准确率） |

人工复核任务由 `scripts/x_stance_v1/review.py` 生成：按日期、语言、主题分层，包含被模型排除的帖子，评审文件里没有模型答案，开发 / 锁定测试各半。

## 重点作者监测（2026-11-23）

- **只是平台内的只读监测名单。** 不会在 X 上关注任何人，不需要 X 密码，不读取私密或付费内容。名单本身是所有者的私有数据，保存在私有库里，不在这个公开仓库中；用 `cli watch import FILE` 导入。
- **一行是一位逻辑作者。** 按规范化用户名去重，同一人出现在多张榜单只合并来源。稳定编号只从供应商返回里 `author.userName` 与用户名一致的帖子绑定，种子文件不能提供编号。绑定后按编号追踪，改名记入用户名历史。
- **有歧义的用户名不查询。** 状态为 `AMBIGUOUS_HANDLE_BLOCKED` 的作者不生成任何查询；所有者只能在已记录的几种写法里确认一种。
- **采集与关键词检索共用近期额度。** 近期 3,000 条 / 0.45 美元内部划分为重点作者 600 / 0.09 和关键词 2,400 / 0.36，不新增额度；预检计入近期池和每日、滚动上限。每位作者先各取一页，再轮流加深，最多三页。
- **三个面板是三个样本版本。** 关键词样本不包含只由作者查询采到的帖子；重点作者面板只包含已绑定编号的作者；合并面板是两者去重后的并集。互不改变统计口径。
- **原文都保留。** 低于点赞下限、没有提到任何标的、被模型判为无关的帖子仍可在阅读页看到，并标明未计入的原因；它们不算中性。
- **调度优先级不是统计权重。** 导入名单不改动任何权重数字。规则里的指定账号通过名单里的别名取得身份。
- **选定时间。** 名单在导入当天选定；更早日期的结果属于事后选定的作者面板。

| 命令 | 作用 |
|---|---|
| `cli watch import FILE` / `watch show` | 导入名单（可重复执行）/ 查看 |
| `cli watch enable ID` / `disable ID` | 启停一位作者，从下一个未冻结的计划起生效 |
| `cli watch confirm-handle ID HANDLE` | 所有者在已记录的写法里确认一个有歧义的用户名 |
| `cli watch verify-candidate ID` | 有密钥时，对每种候选写法各取一页并保存证据；不自动确认 |
