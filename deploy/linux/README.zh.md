# Linux 个人部署运行手册（PR #4，2026-11-18）

本目录是**已在本机 Docker Desktop（Linux 引擎，WSL2 内核 6.18）隔离验收过**的部署路径；尚未部署到任何云服务器。验收回执见 [container_acceptance_redacted.json](../../results/personal_cloud_pr4_linux_20261118/container_acceptance_redacted.json)。

## 组成

| 服务 | 入口（`entrypoint.py`） | 网络 | 说明 |
|---|---|---|---|
| db | PostgreSQL 16 | backend（internal，无端口） | 只保存门户账户、设置、自选、事实卡 |
| migrate | `migrate` | backend | 迁移后退出 |
| web | `web`（gunicorn） | backend + edge | 不发布端口，只经 proxy 访问 |
| proxy | Caddy | edge | 唯一对外端口；HTTPS 在此终止 |
| feed | `feed` | backend + egress | moomoo 推送 + 金十；单实例 fcntl 锁 |
| research | `research`（v42） | **none** | 中文评分；也是 v43 日度快照与标签状态的**唯一**调度者 |
| daily-price | `daily-price`（v45） | egress | 有上限的日线/标签任务 |

`manage.py run_worker` 只清理会话，不作为采集或研究 worker 使用。私人 SQLite（采集缓存、评分、队列、标签）保留 SQLite + WAL，放在本机持久化命名卷 `personal_var`、`private_artifacts`；**不要放 NFS/共享网络盘**。所有应用进程以 uid 10001 运行，根文件系统只读；Caddy 使用官方镜像（容器内 root，仅保留 NET_BIND_SERVICE）。日志 `local` 驱动 10 MB×5 轮转；`restart: unless-stopped`。

健康检查区分业务：`health feed` 要求采集锁被持有且每个启用的供应商心跳 ≤180 s、金十最近成功 ≤15 min、moomoo 最近推送 ≤5 min；`health research` 要求进程身份存活且有积压时最近成功 ≤30 min；`health daily-price` 要求进程存活且状态 RUNNING。`health web` 只代表页面，不代表采集。

## 构建

```sh
docker build -f deploy/linux/Dockerfile --target app -t quant-personal-app:<tag> .
docker build -f deploy/linux/Dockerfile --target research -t quant-personal-research:<tag> .
```

`Dockerfile.dockerignore` 为白名单：仅源码、configs、指定的已入 Git 脱敏回执。构建上下文约 2 MB；镜像中没有数据库、原文、行情、备份、令牌、.env 或模型权重（已扫描）。

## 服务器上需要准备的输入（不入 Git）

1. `/srv/quant/private.env`（0600），由 `env.example` 复制，填写随机数据库密码、SECRET_KEY、域名。
2. 密钥文件（Compose 的 file secret 不支持 uid/mode，按宿主文件权限挂载）：
   `install -o 10001 -g 10001 -m 0400 <file> /srv/quant/secrets/moomoo_token_key`（32 字节随机数 base64：`head -c32 /dev/urandom | base64`），以及金十令牌 `/srv/quant/secrets/jin10_mcp_token`。
3. 模型卷：`docker volume create quant_model_cache_v41`，从受控来源复制 v41 模型 6 个文件，并按 `results/news_sentiment_factor_v41_20261117/input_manifest_redacted.json` 的 SHA-256 核对；worker 加载时只读。
4. DNS 指向服务器后再启动 proxy（Caddy 自动申请证书）。无可信 HTTPS 时不要把 `TRUSTED_REVERSE_PROXY` 改成伪装配置。

## moomoo 授权（Linux 单独授权，不迁移 DPAPI）

Windows DPAPI 密文无法在 Linux 解密，也不复制。服务器使用 `ServerFileTokenStore`（AES-256-GCM，密钥来自上面的 secret 文件）。授权步骤由账户所有者本人完成：

```sh
ssh -L 8790:127.0.0.1:8790 <server>
docker compose -f deploy/linux/compose.yaml --env-file /srv/quant/private.env run --rm feed moomoo-auth authorize --callback-port 8790
```

终端显示的同意链接在**自己的浏览器**打开；回调经 SSH 转发到服务器的回环端口。只接受 `quote:read`。moomoo 公开 Getting Started 文档同样使用 localhost 回调注册；云服务器所在地域是否允许采集与展示仍需账户方确认。HTTP 439 的定时 REST 快照在 Linux 配置中仍为关闭。

## 迁移与单写入者切换

1. 本机：停止 feed、v42、v45（门户可继续只读），用 SQLite 在线备份接口备份全部私人库，`quick_check` 通过后记录哈希。
2. 账户迁移：在本机 `manage.py dumpdata auth.user core --indent 1 > accounts.json`（不含会话），服务器 `migrate` 后 `loaddata`。**主键必须保留**：采集缓存的 `owner_binding` 绑定的是 owner ID，ID 不一致时 feed 会拒绝启动（失败即停）。
3. 把 SQLite 备份拷入 `personal_var` / `private_artifacts` 卷（包含 v42 队列中的 `association_revisions`；缺此表时日度快照会拒绝生成）。核对行数、源哈希、14 张历史事实卡、6 条自选、首个快照 `fa8cb763…` 的截止和可用时间不变。
4. 依次启动 db → migrate → web/proxy → research → feed → daily-price；用 `health <role>` 和真实业务时间判断，不以 HTTP 200 代替。
5. 本机与云端**不得同时**运行 feed / research / daily-price。漏过的截止按协议标记 LATE，不倒填 `signal_available_at`。

## 回滚

停止云端 feed、research、daily-price → 云端在线备份 → 恢复本机唯一实例。云端已形成的前瞻记录保留并带回，不回拨来源时间。

## 隔离验收（已执行）

```sh
docker compose -p quant-pr4-test -f deploy/linux/compose.yaml -f deploy/linux/compose.test.yaml --env-file <scratch>/test.env up -d --no-build
```

测试叠加层关闭两个供应商循环、不启动 daily-price、只发布 `127.0.0.1:18443`；`acceptance_synthetic.py` 只挂载到测试容器，写入的数据都标为 SYNTHETIC。
