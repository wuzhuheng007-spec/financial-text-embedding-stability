# 新闻 N1 有界样本：当日简报

日期：2026-11-14，Asia/Shanghai（UTC+08:00）。研究状态：**初步、描述性**。研究目标是先取得有来源的真实新闻文本，检查公司—事件抽取字段，再在时间与历史身份规则成立后研究预测和金融结果。本简报的数字分别绑定[来源与读取回执](../results/news_n1_20261114/source_sample_receipt.json)、[脱敏样本概况](../results/news_n1_20261114/sample_profile.json)和[字段检查回执](../results/news_n1_20261114/field_review_receipt.json)；原文及逐条草稿仅在本机被忽略的 `artifacts/news_n1_20261114/`。

| 计划 | 实际 | 状态 |
| --- | --- | --- |
| 读取约束和旧 N0 | 已读共享边界、路线入口/状态、未推送的 N0 报告与 JSON、git 状态。工作区未找到磁盘版 `AGENTS.md`，执行了本会话用户给出的同名规则；无关的 M5 未跟踪脚本保持原样。 | 完成 |
| 核验原作者来源 | 固定 [GitHub `4054842`](https://github.com/Zdong104/FNSPID_Financial_News_Dataset/blob/4054842ec476953b30ee874d4b7e8eea786a21fa/README.md) 与 [HF `bf9189c`](https://huggingface.co/datasets/Zihan1004/FNSPID/blob/bf9189c41527198897d1af3e17b1a0095279fc45/Stock_news/nasdaq_exteral_data.csv)，选定原拼写文件 `Stock_news/nasdaq_exteral_data.csv`。 | 完成；许可差异保留 |
| 有界获取真实样本 | 匿名 HTTP 206 分段读取；流式 CSV 解析完整逻辑记录及跨行正文。上限 1,000 条扫描、100 条非空正文、50 MiB 传输。 | 扫描 100 条、保存 100 条后因正文上限停止；客户端实读 737,280 字节。 |
| 检查字段与事件契约 | 核对 12 个实际字段、哈希、缺失、重复、日期与 URL；固定顺序逐条检查前 10 条，生成 11 条私有 Codex 审阅草稿并复核证据位置。 | 工程文本可用；草稿不是独立人工金标准，也不是抽取模型输出。 |

这 100 条保存记录均有标题、候选 ticker、URL 和非空 `Article`；`Publisher`、`Author` 在该样本中均空。100 个 URL 不重复；精确标题 90 种、正文哈希 96 种。四个 `*_summary` 字段与 `Article` 分列；观察到的表头没有情感标签。全部 100 条 `Date` 字符串带秒和字面 `UTC`，其中 99 条恰为午夜。这不证明原始发布时间或盘中可得性。样本只来自目标文件开头，且只有一个候选 ticker；上述比例和计数**不能推广到全库或其他公司**。

原作者 [LICENSE](https://github.com/Zdong104/FNSPID_Financial_News_Dataset/blob/4054842ec476953b30ee874d4b7e8eea786a21fa/LICENSE)及 [HF README](https://huggingface.co/datasets/Zihan1004/FNSPID/blob/bf9189c41527198897d1af3e17b1a0095279fc45/README.md)写 CC BY-NC-4.0，而 GitHub README 的商业使用措辞前后冲突。按其非商业研究声明进行了本机处理；没有推定商业使用或第三方新闻正文再分发权，也没有上传正文。

前 10 条字段检查出现来源日期与文内日期冲突、ticker 与正文公司不对应，以及描述股价结果的文章。这说明时间、公司关联和事件/结果须分开标注；11 条草稿中的方向判断不能用作质量指标。首版私有草稿曾把正文标成可能暗示完整原文的类别，已保留旧版并在第二版改为“`Article` 字段有正文、完整性未知”。

[可复用取样代码](../scripts/sample_fnspid_news.py)和[脱敏画像代码](../scripts/profile_fnspid_sample.py)是整理后的加强版 v2，与实际运行并保存在本机忽略目录的 v1 脚本并非同一字节版本；v1 哈希见来源回执，没有重新下载真实样本。[验证日志](../results/news_n1_20261114/code_validation.log)记录 7 项合成测试通过及 Python 编译通过。

**仍未完成：**独立人工裁定、文章来源及可用时间、历史公司—证券链、完整金融协议。该新闻样本尚未获得正式金融准入；抽取模型推理、抽取质量指标、金融标签、拟合、预测和收益评估均 **NOT_RUN**。下一步请先人工裁定本机 10 条草稿，再冻结输出契约并做有界抽取质量原型；金融使用另需补齐时间与身份依据。
