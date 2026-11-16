# 个人 Demo v1.6：行情一致性与持续新闻分析

执行日期：2026-11-16，Asia/Shanghai。页面：[本机个人门户](http://127.0.0.1:8765/)；仅回环地址和原所有者账户可读。逐项计划、实际与偏差见[台账](../results/quant_portal_personal_v16_20261116/plan_execution.csv)，运行数字及输入代码哈希见[脱敏回执](../results/quant_portal_personal_v16_20261116/runtime_redacted.json)。

## 实际完成

- 变更前对门户、行情新闻、旧分析及已审阅卡片四个私人 SQLite 库做在线备份，逐库 `quick_check=ok`。所有者账户、自选、设置、历史事实卡分别为 1、6、1、14，升级后计数未变；备份本身留在本机忽略目录。[备份回执](../results/quant_portal_personal_v16_20261116/backup_redacted.json)。
- 对 TQQQ、QQQ、SOXX、SPY、NVDA、MU 作了一次官方证券资料批量只读补查。六只名称和股票／ETF 类别有供应商响应依据，系统占位名称才被回填；没有覆盖用户自定义名称。该响应**没有币种字段**，本次报价也不能为六只逐一核实币种，因此页面明确标为供应商未提供，不凭 `US` 前缀填 `USD`。[字段覆盖回执](../results/quant_portal_personal_v16_20261116/security_profiles_redacted.json)；接口身份见 [moomoo 证券基本资料文档](https://open.moomoo.com/api/quote/basic-data/stock-basicinfo)。
- 自选、行情卡和图表头部改用同一按**报价来源时间**选择的结果；迟到旧包的接收时间不能把旧价格变成最新价。六只证券在**同一个 SQLite 只读快照**中逐项一致。首次审计用两次顺序读取，NVDA 恰好遇到缓存更新而误报不一致；已保留旧回执并[修正审计方法](../results/quant_portal_personal_v16_20261116/audit_correction_redacted.json)，未修改行情原件。主卡区分报价时段与常规市场状态；夜盘报价不会再被常规市场的盘后结束码贴成“夜盘停止”。官方夜盘交易日归属和账户延迟标志仍缺字段证据，详情里保留原始状态及来源时间。[行情状态回执](../results/quant_portal_personal_v16_20261116/runtime_redacted.json)；状态码与报价字段按 [moomoo 市场状态](https://open.moomoo.com/api/quote/basic-data/market-state)及[实时报价](https://open.moomoo.com/api/quote/realtime/stock-quote)文档核对。
- 新闻主卡显示“来源：金十”。没有供应商标题时使用原文首个完整句子，并标明“摘录”；本次显示窗口 47 条中，45 条使用该规则，1 条为不完整摘录，1 条有供应商标题。报道署名与已经打开核实的原始发布者分开，未独立回查的消息继续标“待交叉核实”。不删除旧私人新闻。[新闻窗口统计](../results/quant_portal_personal_v16_20261116/runtime_redacted.json)。
- 停止旧 v1.5b 分析写入进程，启动独立的本机 Qwen 持续 worker。任务键绑定新闻版本、原文哈希、模型 revision、提示词与 Schema；旧失败任务可进入新版队列，同版本再次入队得到零新任务。生成超时和次数有上限；旧原始响应、失败及 v16.1—v16.4 候选均保留在私人库。当前运行契约是 `personal_news_conditional_v16_4`。[分析审阅记录](../results/quant_portal_personal_v16_20261116/analysis_review_redacted.json)。
- 页面没有合格 AI 内容时显示来源摘录、相关性短语与**规则提示**，不再给大块空白框。经旧版同团队审阅接受的卡片继续有独立标签；已拒绝的旧输出不会自动重新标为合格。新闻语气与已观察行情分列，Top 10 是规则排序。[浏览器检查回执](../results/quant_portal_personal_v16_20261116/browser_acceptance_redacted.json)。

## 没有完成的内容验收

当前 v16.4 旧失败记录有界重处理仍落到回退：出现缺少反向条件、JSON 不完整等错误。先前 v16.2 的 7 条机械通过草稿经同团队阅读，4 条发现不适合展示，其中 1 条把提示词里的虚构计划搬进真实新闻；余下 3 条只是有限的条件式草稿，**不是独立核实的事实**。已移除该示例并加入污染检查，但不能把软件检查写成内容通过。持续 worker 正在处理其余队列；[回执](../results/quant_portal_personal_v16_20261116/runtime_redacted.json)记录某一时刻的分母，不能外推为长期成功率。模型拟合、金融收益预测和交易均为 0／`NOT_RUN`。

中文、英文桌面及 390px 布局在 v16.3 页面实看，行情、新闻摘录、空状态与折叠布局可读；英文供应商原文尚未逐条翻译，会显示“待翻译”和原文。最后一次 v16.4 重启后的浏览器复看碰到用户同时操作 Edge，停止了电脑操作。因此**最终代码的完整视觉复验尚未完成**；HTTP 200、10 项定向测试和 14 项 Django 测试不能替代它。未登录与非所有者访问由 Django 定向测试覆盖。

补充：快照线程曾以 `sqlite3.OperationalError` 停止，WebSocket 和金十仍独立运行。为快照线程增加最多三次、带等待的 SQLite 恢复后重启采集，三条线程在末次回执时均活跃；这仅证明恢复后的当前状态，不证明长期稳定。moomoo 英文新闻源尚未接入个人新闻流。

## 下一步

1. 在浏览器空闲时对最终 v16.4 再做一次桌面和 390px 实看。
2. 从保存的 v16.4 失败中固定一类问题作小批量内容审阅；先确认事实支撑，再决定是否提交新提示词。当前来源摘录与规则提示继续可用。
3. 若确需币种、官方夜盘交易日或供应商延迟状态，取得对应**明确字段或授权资料**再填写；价格与来源时间现在仍可读。

财务输入 v2.5 继续为 `REJECTED_CANDIDATE`。本轮没有训练、收益评价、交易、云部署或公开数据发布。
