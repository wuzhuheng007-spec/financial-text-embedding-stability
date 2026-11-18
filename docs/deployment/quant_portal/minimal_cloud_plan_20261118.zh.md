# 最小云端方案与采购准备（PR #4 第三轮，2026-11-18）

**本文不是购买指令。** 服务器未购买、未部署。金十在云端保持关闭，且不是部署私人网站的前提；本机金十缓存**不会**被同步到云端。

## 1. 最小部署：网站 + 已授权行情

| 组件 | 云端 | 说明 |
|---|---|---|
| db / migrate / web / proxy | 运行 | PostgreSQL 账户、所有者隔离、Caddy HTTPS |
| feed（仅 moomoo 推送） | 运行，**需 Linux 上的独立 `quote:read` 授权** | `configs/quant_portal_personal_feeds_linux_v1.json`：moomoo 开、金十关、REST 快照关（HTTP 439） |
| moomoo-auth | 一次性（profile `auth`） | 回调端口只发布在服务器 127.0.0.1:8790，经 `ssh -L` 使用 |
| research（v42 评分、v43 日度快照） | **不在云端运行** | 输入是金十新闻；云端没有获许可的新闻源时它没有输入 |
| daily-price（v45） | **不在云端运行** | 服务于 v43 快照的标签；快照仍在本机产生 |
| Jin10 | 关闭 | 需金十书面许可后才能用 `compose.jin10.yaml` |

启动（服务器上，私人 .env 与密钥文件就绪后）：

```sh
docker compose -f deploy/linux/compose.yaml --env-file /srv/quant/private.env up -d db migrate web proxy feed
docker compose -f deploy/linux/compose.yaml --env-file /srv/quant/private.env --profile auth run --rm --service-ports moomoo-auth
```

不需要 `JIN10_MCP_TOKEN_PATH`；矩阵行 CFG-1 验证了不含金十密钥时 Compose 可以渲染，CFG-2 验证金十叠加层缺少令牌路径会拒绝渲染。

## 2. 没有云端新闻源时，新闻与情绪的实际范围

- 云端页面：显示 moomoo 行情、自选、设置、历史事实卡；**新闻列表、Top 10、新闻语气、日度快照在云端为空或停止更新**，页面需把“云端无新闻源”显示为状态，而不是“今天没有新闻”。
- 前瞻研究（v42/v43/v45）继续在本机运行，单一写入者不变；首个快照 `fa8cb76334c94f7560aab543c99cf9f9` 和后续截止仍由本机 v42 调度。
- 行情单写入者：云端 feed 启动前，本机 feed 的 moomoo 部分必须停止。当前本机 feed 一个进程同时跑金十与 moomoo；要“本机只留金十、云端只跑 moomoo”，需要本机用一份关闭 moomoo 的配置重启 feed（本轮未做，属于切换步骤，需用户确认时间窗口）。
- 本机研究的价格标签依赖 v45（本机，moomoo REST 日线，额度 8/天）；云端 moomoo 推送与本机 v45 使用同一账户的不同接口，是否同时允许、是否共享额度需在账户内确认。

## 3. 采购前需要用户确认的输入（不含任何历史报价）

| 输入 | 现状 |
|---|---|
| 账户站点（中国站 / 国际站）与实名 | 未确认 |
| 地域（候选：新加坡）与库存 | 候选来自用户页面，未在结账页核实 |
| 套餐（候选：4 vCPU / 8 GiB / 70 GiB / Ubuntu 22.04） | 用户页面报价 ¥156/月；**结账总价、税费、流量包、自动续费未核实**；历史 USD 35/月不是当前订单价 |
| 域名与 DNS | 未提供 |
| moomoo 账户适用条款主体、行情权限、云端使用确认 | 未确认（见 [供应商条款](supplier_cloud_terms_20261118.zh.md)） |
| moomoo Linux 授权 | 服务器存在后由用户执行（见下） |
| 金十书面许可 | 未取得；不影响最小方案 |

容量判断见 [disk_sizing_r3_redacted.json](../../../results/personal_cloud_pr4_linux_20261118/disk_sizing_r3_redacted.json)：70 GiB 只有在启用行情保留策略后才有把握；CPU/内存只有本机 Docker Desktop 的单次负载测试，不能证明 4 vCPU 足够。

## 4. 用户要做的 moomoo 授权步骤（令牌不进入聊天、Git 或日志）

1. 服务器上生成密钥文件：`head -c32 /dev/urandom | base64 | sudo tee /srv/quant/secrets/moomoo_token_key >/dev/null && sudo chown 10001:10001 /srv/quant/secrets/moomoo_token_key && sudo chmod 0400 /srv/quant/secrets/moomoo_token_key`。
2. 在自己的电脑：`ssh -L 8790:127.0.0.1:8790 <user>@<server>`（保持连接）。
3. 在服务器另一个终端：`docker compose -f deploy/linux/compose.yaml --env-file /srv/quant/private.env --profile auth run --rm --service-ports moomoo-auth`。
4. 终端打印同意链接；在**自己电脑的浏览器**打开，只授权行情读取。浏览器回调 `http://127.0.0.1:8790/callback` 经 SSH 转发到服务器回环端口，再到授权容器。容器收到一次回调后退出并释放端口。
5. `docker compose ... run --rm feed moomoo-auth status` 应显示 `QUOTE_READ_AUTHORIZED`、`SERVER_AES256GCM_KEYFILE`。
6. 若 moomoo 不接受 `http://127.0.0.1:8790/callback` 形式的回调，停止并改用开发文档中的 API Key 方式，不绕过。

本机已用**模拟**提供方验证：端口发布、容器内绑定、state 校验、提供方错误回调、redirect_uri 精确匹配、PKCE、退出后端口释放；**SSH 跳转一段未在本机执行**，moomoo 真实授权未执行。

## 5. 本机 C 盘与 Docker（只读结论，未删除任何东西）

见 [docker_disk_inventory_redacted.json](../../../results/personal_cloud_pr4_linux_20261118/docker_disk_inventory_redacted.json)。Docker 虚拟磁盘 `docker_data.vhdx` 在 C 盘占 90.6 GB；构建缓存合计约 60 GB，其中最大的是约 3 个月前另一项目（bimq）的 venv 层；本项目今天产生的、未与镜像共享的缓存约 4.3 GB。可选方案（需用户确认后执行）：

1. **只删本项目未共享缓存（约 4.3 GB，不影响其他项目）**：`docker buildx prune --filter id=gw0rv1c2829vjdoehr6vglj6s --filter id=it6oz9o8xfhkvm383q7gajvna --filter id=v98zq6yye2hvhbiwagbk8wnrx`（ID 以执行时 `docker buildx du --verbose` 为准）。影响：下次构建 research 镜像需重新下载 torch。注意：缓存在 Linux 虚拟机内被删除后，Windows 上的 vhdx 文件不一定自动缩小。
2. **把 Docker 磁盘镜像位置迁到 D 盘（429 GB 可用）**：Docker Desktop → Settings → Resources → Advanced → Disk image location。影响：迁移期间 Docker 停止；不删除任何镜像或卷。这是释放 C 盘最有效的方式。
3. **清理 bimq 等其他项目的旧缓存或镜像**：会影响那个项目的重建速度，必须由用户逐项决定。**不执行 `docker system prune -a --volumes`。**

在 C 盘恢复空间前，不再重建大型镜像；本轮验证使用已有镜像并通过 `compose.devmount.yaml` 挂载当前代码（仅测试用）。
