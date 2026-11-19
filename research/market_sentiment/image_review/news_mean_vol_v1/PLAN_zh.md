# 新闻情绪均值与波动：本轮学习计划

研究问题：截图中的情绪均值、时间标准差及其乘积各测量什么；其个股行业相对收益证据能否支持本项目的市场中期拐点研究。

本轮单位为方法定义、截图转述和确定性数学例子，不是证券回测。准许结论仅限定义及可辨识性；不检验收益显著性，不训练模型，不改变第三、四阶段结果。

|计划|操作与输入|输出|验收及可支持的结论|
|---|---|---|---|
|P1 阅读及追溯|阅读IMG_1583—IMG_1592；保留本地输入哈希；有限网络检索原出处|input_manifest.json、source_audit.json|截图数字标记USER_REPORTED_NOT_VERIFIED；未定位原文时不猜作者/数据商|
|P2 定义核对|直接打开NumPy nanmean/nanstd及SciPy spearmanr官方文档|source_audit.json、REVIEW_zh.md|区分标准差/方差、时间/截面和相关/净收益；官方API页不支持金融功效|
|P3 数学诊断|运行python formula_audit.py；仅用toy_config.json内明确标识的人造数组|formula_results.json、formula_run_manifest.json|验证时间倒序不变、计分原点敏感、缺失样本与同日分歧的反例；同作者检查，不是独立金融复制|
|P4 研究落点|写候选合同、证据台账、计划实际对照；追加真实研究日志；运行仓库发布校验，非强制提交并读回|REVIEW_zh.md、CANDIDATE_CONTRACT_zh.md、evidence_ledger.json、AUDIT_FINAL.json、仓库日志|候选明确PENDING_INPUTS；保留旧结果和其他目录；读回后才称同步完成|

P1/P2是计算前进行的只读学习。toy_config.json及本计划在P3执行前写入；这不是金融实验预注册，也不消除已经阅读截图后形成候选的选择过程。截图的30日设定是待追溯规格，不是本项目已接受的最佳窗口。

阻碍金融复现的条件：原研究身份/代码与原始数据尚未取得；逐条新闻历史可用时刻、去重规则及打分版本未通过验收；市场整体择时目标与截图横截面目标不同。
