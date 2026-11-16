# 个人 Demo v1.5d：新闻分析证据与双语修订

**执行日期：**2026-11-16（Asia/Shanghai）。**范围：**既有本机个人门户、原 20 条重复使用的金十开发新闻、固定 Qwen2.5-3B-Instruct revision `aa8e72537993ba99e69dfaafa59ed015b17504d1`。没有训练、收益预测、交易或公共展示。本轮结果不能推论新闻分析的一般准确率。

## 目标、实际和有限结论

原 v1.5b 的 20 条开发消息中有 8 条机械通过，但[同团队核源](../results/quant_portal_personal_v15_20261116/semantic_review_redacted.json)全部驳回。此次先把模型自行抄写的证据引文改为证据段 ID，由程序回填同一原文版本的逐字证据。中间版 v1.5c 在[固定输入](../results/quant_portal_personal_v15c_20261116/input_manifest_redacted.json)上只完成 8 条结果文件、14 条有记录的生成尝试；4 条机械通过却因英文人名误译等被[全部驳回](../results/quant_portal_personal_v15c_20261116/semantic_review_redacted.json)，因此停止扩跑。旧脱敏回执少计了一条原因频次；[修订对账](../results/quant_portal_personal_v15c_20261116/semantic_review_reconciliation_redacted.json)直接从冻结的私人审阅记录重算，旧回执未改。第 9 条请求曾准备，但没有保存的响应，不能计为完成调用。[负面运行回执](../results/quant_portal_personal_v15c_20261116/run_redacted.json)保持原样。

v1.5d 对原文中明确的中文发言人名称使用占位符；模型必须在中英文中原样保留占位符，展示层才按同一原文版本确定性恢复名称。原始响应、恢复后的展示视图和同团队审阅分别留存。此规则只覆盖明确的单一发言人格式，不能解决所有实体翻译。运行前已冻结[配置](../results/quant_portal_personal_v15d_20261116/run_config.json)和[输入/代码哈希清单](../results/quant_portal_personal_v15d_20261116/input_manifest_redacted.json)；20 条完整提示词均在 2,048-token 上限内，最大 922 tokens，见[预检](../results/quant_portal_personal_v15d_20261116/token_preflight_redacted.json)。

[v1.5d 真实运行](../results/quant_portal_personal_v15d_20261116/run_redacted.json)记录 **20/20 条尝试、30 次真实生成、11 条机械完成、9 条失败**。11 条机械完成结果经逐条同团队来源审阅，[接受 2 条、驳回 9 条](../results/quant_portal_personal_v15d_20261116/semantic_review_redacted.json)。驳回原因包括英文残留中文、实体误译、缺证据的政策推断、相关性薄弱及过度概括。接受只表示分析措辞与该供应商文本的有限证据相容；金十报道本身**未经独立事实核验**。v1.5b、v1.5c、v1.5d 同用开发材料，且改动包含证据契约、占位处理和提示词，不能将 0 到 2 的变化归因于单一修改。

两条接受的分析被导入**独立的私人 SQLite 审阅缓存**，只在登录所有者、消息版本和内容哈希匹配时展示；其余仍显示真实待审、失败或证据不足状态。页面仍保留原文与供应商归属，不会将审阅通过写成官方认证。[本机只读回执](../results/quant_portal_personal_v15d_20261116/runtime_redacted.json)在记录时返回 HTTP 200、六只证券缓存、十个 Top 10 位置和两条可显示的审阅卡；moomoo 和金十心跳分别记录。这是该时点的本机状态，不代表长期可用性或页面视觉验收。

用户提醒金十快讯通常为中文，moomoo 可能提供英文新闻。先前[来源探测](../results/market_observer_historical_20261115/news_official_redacted.json)确有一次 moomoo 新闻搜索返回，但该接口**没有接入当前个人新闻库**，返回文本的英文比例和持续权限尚未本轮核验。后续可对授权的 moomoo 英文原文做有界接入检查；必须保留发布者、转载关系和原文语言，不能把一中一英自动当作独立交叉确认。当前英文分析均标为模型生成；未完成英文显示“待翻译”，切换语言不会重新调用供应商或模型。

## 验证与剩余缺口

[19 项定向 Python 检查](../results/quant_portal_personal_v15d_20261116/focused_tests.log)和[24 项 Django 隔离测试](../results/quant_portal_personal_v15d_20261116/django_tests.log)通过。最初两次 Django 启动因临时测试 `SECRET_KEY`、`ALLOWED_HOSTS` 未满足项目安全设置而失败，失败日志保留；调整的是测试进程环境，并未放宽生产设置。软件检查不等于新闻内容正确。新的桌面及 390px 登录后视觉验收仍为 `NOT_RUN`，不能沿用 v1.4 的旧结果。完整双语内容仅 2 条通过同团队开发审阅，其他 18 条不得显示为可信分析。

下一步优先检查 moomoo 新闻源的实际英文内容与授权条件，并在独立开发批次中处理多发言人、非固定格式人名和新闻主体误译；对登录页面补做桌面及 390px 实看。财务输入 v2.5 保持 `REJECTED_CANDIDATE`，正式金融实验仍为 `NOT_RUN`。逐阶段计划与实际见[台账](../results/quant_portal_personal_v15d_20261116/plan_execution.csv)。私人原文、模型原响应、审阅全文和数据库均未公开。
