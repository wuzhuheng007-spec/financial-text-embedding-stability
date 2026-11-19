# 重跑与原始证据说明

本阶段公开代码、全部规则事件及研究汇总。原行情、原始风险序列、逐日面板、逐行预测和训练参数不提交GitHub；输入SHA256见input_manifest.json和config.json。不能仅凭当前在线下载替代冻结版本。

## 私有证据包的目录

解压后保留以下相对结构：

```
moomoo_research_dataset_20261119/dataset_v0_1/market_research.sqlite
sentiment_phase3_20261119/config.json
sentiment_phase3_20261119/freeze_receipt.json
sentiment_phase3_20261119/raw/
sentiment_phase3_20261119/private/
sentiment_phase3_20261119/private_v1_1/
sentiment_phase3_20261119/results/
sentiment_phase3_20261119/results_v1_1/
```

数据库来自此前授权行情验收版本；本轮未取得持续API连接。日线前复权不等于核验过的含分红总收益。raw的美国序列是本轮固定的当前版本，不具备历史PIT认证。

## 验证已有运行

1. 使用Python、numpy、pandas、scipy、matplotlib；实际Python/numpy/pandas版本在run_manifest.json，其他包版本见environment.json。
2. 在私有目录布局完整的前提下执行 `python sentiment_phase3_20261119/self_check.py`。该脚本从原SQLite重新核对价格、特征和未来标签，从原来源文件核对日期，并核对保存的模型参数/预测。
3. 执行 `python sentiment_phase3_20261119/verify_report.py`，核对全部225行模型汇总、159行对照、756行候选信号汇总、968行事件轨迹与展示数字。行数是本轮保存产物身份，不是独立市场样本数。

## 重新计算

不要覆盖已有结果。复制完整私有目录到新工作目录，保留原目录不动；仅在新副本中将 `results_v1_1/` 和 `private_v1_1/` 改名保留后，运行 `python sentiment_phase3_20261119/analyze.py`。脚本会拒绝覆盖已有run_manifest。重算后执行上述两项核对。

`freeze_plan.py` 是本轮计算前的历史冻结动作。现有config和freeze_receipt保持原样，不应重跑以伪造新的“事前”记录。首轮源码为audit/analyze_v1.py；边界修正为AMENDMENT_v1_1.json，分析结论使用v1_1。

`collect_official_calendar.py` 重跑将得到新的网页快照，不能宣称与原哈希必然相同。原官方网页索引快照包含在私有证据包中；statement_links.csv只认证链接提取，未认证所有正文或历史首次公开时刻。

`make_report.py` 将从已保存结果生成展示表、全部事件卡与轨迹图。图按事后极值对齐，不能作为实时信号使用。保存的复核是同作者审核，没有独立审计身份。
