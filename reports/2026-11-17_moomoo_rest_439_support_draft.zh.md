# moomoo WebAPI 只读报价接口安全策略阻断：待发送询问草稿

**本文件仅为草稿，未发送。** 个人本机只读应用的 `POST https://webapi.moomoo.com/api/v1.0/quote/stock-quote` 曾返回 HTTP 439；本机记录的脱敏错误类别为 `WEBAPI_SECURITY_POLICY_BLOCKED`，最后一次成功的该端点业务请求为 2026-11-17 08:27:20 UTC。此后该端点已暂停，没有通过换账户、IP、通道或增加权限重试。独立 WebSocket 推送与历史日 K 路径仍有各自状态，不能据此推断此端点已获准恢复。

本机加密凭据的状态读取为 `QUOTE_READ_AUTHORIZED`，返回的权限类别仅为 `quote:read` 和脱敏账户选择标识；在 2026-11-17 09:53 UTC 左右检查时，当前 access token 的过期时间记录为 11:21:20 UTC。这里不附账户、令牌或原始响应。官方文档将 HTTP 439 / `-12009` 解释为 WebAPI 安全策略阻断，并建议检查该 API 是否获授权；这**不能单独证明**具体原因是 scope、行情套餐、风控或其他项。文档：<https://open.moomoo.com/api/overview/getting-started>；端点：<https://open.moomoo.com/api/quote/realtime/stock-quote>。

可由用户在 moomoo 官方支持渠道发送以下问题，并在官方渠道提供必要的脱敏请求时间与账户标识：

> 我在本机使用 moomoo WebAPI 只读行情，OAuth 授权包含 `quote:read`。`POST /api/v1.0/quote/stock-quote` 此前成功，随后返回 HTTP 439（文档对应安全策略阻断）。请确认此端点当前是否对我的 WebAPI 客户端和账户开放、是否需要额外的**只读行情**权限或行情订阅，以及是否存在可核对的安全策略限制。请说明解除限制所需的官方操作；我不申请交易、持仓或写入权限。

在官方确认或授权状态有明确变化之前，程序保持该 REST 端点暂停。本草稿不得作为授权已经恢复的证据。
