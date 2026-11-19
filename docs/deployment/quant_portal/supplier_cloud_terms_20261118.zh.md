# 供应商云端使用条款与真实授权步骤（2026-11-18 核对）

本文只依据公开原文。引文为页面原句或原句片段（片段已注明）。账户所属法律主体、已签署的协议和账户行情权限只能由账户所有者在自己的账户中确认，本文不作推定。**这不是法律意见。**

## 结论

| 供应商 | 云服务器上仅所有者私用 | 判断依据 | 状态 |
|---|---|---|---|
| moomoo WebAPI | 技术上被文档明确支持；条款要求行情仅个人使用、不得再分发 | 见下 A | 有条件可行，**需账户方确认所属主体条款与行情权限** |
| 金十 MCP | 用户协议限定“仅可作为私人和非商业用途而保存在某台计算机内”，并禁止未经书面授权“在非金十数据APP所属的服务器上做镜像” | 见下 B | **阻塞：需金十书面确认或授权后才能在云端缓存** |

两者都不允许把数据展示给他人；本部署保持所有者单账户、公共行情关闭。

## A. moomoo

1. 开发文档 Getting Started：<https://open.moomoo.com/api/overview/getting-started>
   - “Use this method for server-side systems, scheduled server jobs, internal tools”（API Key 方式的适用说明）
   - “store `refresh_token` securely and let your Linux cloud service refresh and call APIs automatically”
   - `quote:read`：“Read core market data such as real-time quotes and historical candlesticks”
   - 439 / -12009：“Blocked by WebAPI security policy”，处理建议 “Check that this API is authorized”
   - redirect_uri：“Must exactly match one of the `redirect_uris` used when registering the OAuth client”；示例为 `http://localhost:60355/callback`。

   判断：在 Linux 云服务上保存 `refresh_token` 并自动刷新是文档描述的用法；本仓库的 `ServerFileTokenStore` + SSH 转发回环回调符合这一流程。HTTP 439 属于“安全策略阻止，需确认该 API 已授权”，因此定时 REST 快照继续暂停，不以换令牌、换 IP 或重试绕过。

2. 行情权限与额度：<https://open.moomoo.com/api/overview/api-authority>
   - “Account quote entitlements determine WebAPI totals.”
   - “Actual account entitlements and service policy determine limits.”
   - “NYSE Arcabook depth requires completing the non-professional user questionnaire first”

   判断：订阅额度（本地预算 6 个）和可用行情级别取决于账户权限，需在账户内核对。

3. 用户条款（中文站点链接，正文为英文）：<https://www.moomoo.com/hans/terms>
   - Schedule I 第 1 条（片段）：“Market Data provided herein is for the exclusive use of you for your personal use”
   - 同条（片段）：“You are expressly prohibited from redistributing or otherwise disseminating the said Market Data”
   - 第 13 条（片段）：“only for your personal, non-commercial use”；“No part of the Service or Content may be copied, reproduced, republished, uploaded, posted, publicly displayed”
   - 第 8 条（片段）：“Systematically download and store App content, or use any robot, spider, site search/retrieval application”

   判断：所有者本人在私有页面查看符合“personal use”；任何共享、公开展示或给他人账户均不允许。第 8 条针对 App 内容；WebAPI 由开发文档另行规定。**该条款页是否就是你账户适用的主体（美国/新加坡/澳洲等）未核实**，需在账户的协议记录中确认。

## B. 金十

1. 官方接入指南：<https://mcp.jin10.com/app/doc.html>
   - “点击首页的"立即体验"，登录金十数据的帐号。”
   - “登录成功后，点击"激活"”
   - “MCP Token 申请成功，一键复制后，粘贴到您的AI应用中即可。”
   - 页面未写明令牌有效期、调用上限、使用范围，也没有单独的 MCP 协议链接。

2. 用户协议：<https://cdn.jin10.com/app_pages/user_agreement.html>
   - 一、特别提示：“用户直接或通过各类方式（如 RSS 源和站外 API 引用等）间接使用金十数据APP服务和数据的行为，都将被视作已无条件接受本协议全部内容；”
   - 六、知识产权：“所有这些资料或资料的任何部分仅可作为私人和非商业用途而保存在某台计算机内。”
   - 六、知识产权：“未经金十数据APP及/或相关权利人明确书面授权，任何人不得复制、转载、摘编、修改、链接、转帖金十数据APP的内容，或在非金十数据APP所属的服务器上做镜像或以其他任何方式进行使用。”

   判断：协议通过“站外 API 引用”覆盖 API 使用。本机私人缓存与“仅可作为私人和非商业用途而保存在某台计算机内”相符；**把快讯持续保存到租用的云服务器**可能被视为“在非金十数据APP所属的服务器上做镜像”，原文要求书面授权。在取得金十书面答复前，云端金十采集保持关闭（Linux 配置可单独设 `jin10.enabled=false`），本机继续作为唯一金十采集者。

## 真实授权尚缺什么及步骤

| 缺项 | 谁来做 | 可执行步骤 |
|---|---|---|
| moomoo 账户适用条款与行情权限 | 用户 | 登录 moomoo，在“我的—设置/协议”或开发者平台查看已签协议、OpenAPI 问卷状态与美股行情级别；截图留存于本机私人目录 |
| moomoo Linux 授权 | 用户在服务器上（服务器存在后） | ① `install -o 10001 -g 10001 -m 0400` 放置 `moomoo_token_key`；② `ssh -L 8790:127.0.0.1:8790 <server>`；③ `docker compose -f deploy/linux/compose.yaml --env-file /srv/quant/private.env --profile auth run --rm --service-ports moomoo-auth`（第三轮更正：旧命令没有端口映射）；④ 在自己浏览器打开终端里的链接，只勾选行情读取；⑤ `docker compose -f deploy/linux/compose.yaml --env-file /srv/quant/private.env run --rm feed moomoo-auth status` 应显示 `QUOTE_READ_AUTHORIZED` 与 `SERVER_AES256GCM_KEYFILE` |
| 439 端点 | 用户联系 moomoo | 询问 `POST /api/v1.0/quote/stock-quote` 返回 439 所需的授权；收到明确答复前不重新启用 |
| 金十云端许可 | 用户联系金十 | 书面询问：个人账户的 MCP Token 能否在本人租用的云服务器上调用并保存快讯供本人私有查看；保存答复 |
| 金十令牌放到服务器 | 用户（获许可后） | 在 mcp.jin10.com 登录→激活→复制令牌；在服务器执行 `sudo sh -c 'umask 077; cat > /srv/quant/secrets/jin10_mcp_token'`，粘贴后 Ctrl-D，再 `sudo chown 10001:10001 /srv/quant/secrets/jin10_mcp_token && sudo chmod 0400 /srv/quant/secrets/jin10_mcp_token`；不经聊天或 Git |
| 单写入者切换 | 用户确认后执行 | 见 [运行手册](../../../deploy/linux/README.zh.md)“迁移与单写入者切换”；本机与云端不得同时运行真实采集 |

## C. SEC EDGAR（2026-11-19 补充；北京时间 05:00 查询）

- 官方页面：<https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data>（2026-11-19 05:00:37 北京时间读取，HTTP 200）。
  - "Please declare your user agent in request headers"，示例形式为 "Sample Company Name AdminContact@<sample company domain>"。
  - "Fair access Current max request rate: 10 requests/second."
- 本部署的做法：
  - 只读取公开元数据接口 `company_tickers_exchange.json`、`company_tickers_mf.json`、`data.sec.gov/submissions`，不下载申报正文。
  - 每批 8 个请求，间隔不小于 0.25 s（不超过 4 次/秒），每 6 小时一次。
  - User-Agent 来自用户本人已配置的 `SEC_USER_AGENT`，只保存在服务器 `/srv/quant/secrets/sec.env`（0600），不写入数据库、日志或报告。
- 不确定性：
  - 上述页面规定的是访问方式和频率，没有单独说明再展示的条件。
  - 本部署只在所有者私有页面展示申报元数据并链接回 SEC 原文，没有对外公开。

## 判断依据的时效

- A（moomoo）和 B（金十）的原文是 2026-11-18 查询的，页面可能已经更新。云端启用前必须重新核对。
- 判断"金十书面许可才可云端缓存"、"moomoo 云端使用待账户方确认"属于对条款的解释，不是法律意见。在得到供应商的书面答复之前，按更严格的解释处理：两者都不在云端启用。
