# 2026-11-27 首个应收时段验收（盘前，北京时间 17:00 起）

本目录由本机一次性只读观察任务在 17:12 后写入：`RECEIPT_redacted.json`（收据）和 `health.jsonl`（17:00–17:12 每 20 秒的健康结论）。

- **分母：** 16:59 读到的已确认订阅集合，按排序编为 S1…Sn，映射不公开。
- **匹配：** 17:11 复核时用带盐键逐个匹配同一标的，盐和键都不公开。
- **每个基线订阅只落一种状态：** RECEIVED（计入）、SUBSCRIBED_NO_VALID_QUOTE、DROPPED。基线之后新增的订阅记为 N1…，不进分母。
- **结果格式：** `X/N ... dropped d, still subscribed without a valid quote s, new n`。
- **失败类别：**
  - `OBSERVATION_MISSED`：基线或现场健康缺失，改为只读回查缓存，收据写明现场健康缺失；
  - `FEED_NO_VALID_QUOTE`：没有一个基线订阅收到有效报价；
  - `RECEIPT_PUSH_FAILED`：推送失败，收据只留在本机，本目录不会出现。
- **脚本：** `scripts/quant_personal_feed/acceptance_20261127/`。不含标的、报价数值、账户或服务器地址。
