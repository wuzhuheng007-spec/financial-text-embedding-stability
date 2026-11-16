# 本机个人版行情与快讯接入回执

**执行日期：**2026 年 11 月 16 日，Asia/Shanghai。页面为 <http://127.0.0.1:8765/>；财务输入 v2.5 暂停修复，仍是 `REJECTED_CANDIDATE`。

已复用本机只读凭据，单个 moomoo WebSocket 连接确认订阅 TQQQ、QQQ、SOXX、SPY、NVDA、MU，并将六只证券的真实报价写入只限现有所有者访问的私人缓存。验收期间美股休市；来源时间为 11 月 13 日的旧报价，页面与本机收到时间分开显示，不称为实时成交。币种、延迟及账户订阅配额尚未核实。详见[脱敏运行回执](../results/quant_portal_personal_feed_20261116/runtime_redacted.json)。

金十客户端重新完成 MCP 握手与工具清单读取，按本机实际 `list_flash` Schema 读取最新一页；60 秒后台周期已实际重复运行并对重复快讯去重。[所有者页面回读](../results/quant_portal_personal_feed_20261116/portal_readback_redacted.json)当时返回 33 条已缓存快讯和六只报价。页面每 5 秒只读本机缓存，刷新不会直接请求供应商。“全部快讯”和“我的关注”分别显示缓存全量与别名/宏观规则匹配项；金十报道保持“待交叉核实”。每日最多 1,440 次是**本机请求上限**，不是已核实的金十配额。

现有账户、自选股、设置和 14 张历史事实卡与变更前私人备份逐表一致。未登录接口转到登录页，另一测试账户得到 404；重复启动未创建第二个采集进程。停采后缓存仍在，moomoo 退订获得确认，随后已重新启动。公开行情展示仍关闭；未读取持仓或资产，未调用交易接口。

**未完成：**电脑控制工具无法确认浏览器 URL，因而本轮桌面与 390px 实际视觉验收为 `NOT_RUN`；美股开市时的连续更新也未观察。软件与真实来源检查分别见[测试回执](../results/quant_portal_personal_feed_20261116/test_summary_redacted.json)，各阶段计划—实际见[台账](../results/quant_portal_personal_feed_20261116/plan_execution.csv)。这些结果不证明新闻事实、模型抽取或金融预测有效。接入依据参照 [moomoo 推送认证](https://open.moomoo.com/api/quote/push/auth)、[订阅说明](https://open.moomoo.com/api/quote/push/subscribe)和[金十 MCP 文档](https://mcp.jin10.com/app/doc.html)；实际字段以本机响应为准。

在仓库根目录运行以下命令管理个人版；完整步骤见[中文使用说明](../docs/quant_portal/personal_user_guide.zh.md)：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/start_personal.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/status_personal.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/stop_personal.ps1
```

模型拟合、推理、正式金融标签与回测均未运行。
