# M1/M2 发布与回滚手册（发布候选，未在生产执行）

版本：发布候选 2026-11-20。分支 `codex/m1m2-release-candidate-20261120`。

## 版本对应

精确值以发布包内的 `RELEASE_MANIFEST.json` 为准，本文用 `<…>` 引用，不在此手抄。

| 部分 | 来源 | 说明 |
|---|---|---|
| 运行镜像 | `runtime.commit`（`6fe5001`） | 已封存两个镜像：数值作业镜像和 app 镜像 |
| 交付文件 | `delivery.commit` | 主机脚本、手册与测试 |

两者之间只改了 `tests/`、`docs/` 和 `deploy/linux/host/m1m2_release_env.sh`。这些路径都不在任一镜像的构建白名单内，所以不重建镜像。

## 已演练与未覆盖

- **本机隔离环境**：启动、回滚、恢复路径（第 12 节）。
- **临时 shell smoke**：本手册的命令（第 13 节）。
- **未覆盖**：服务器 systemd 自然触发、主机重启、跨机器传输，以及任何生产执行。

## 0. 固定值与执行方式

| 项 | 值 |
|---|---|
| 访问 | 只用现有 SSH（admin + sudo）；不开放 80/443/8443 或数据库端口 |
| 部署目录 | `/srv/quant/app/deploy/linux`（compose.yaml 所在，不是 git 检出） |
| 私有环境文件 | `/srv/quant/private.env`（root 0600）。只用 `sed` 改两项镜像名；只用 `grep -E '^(APP_IMAGE\|NUMERIC_IMAGE)='` 查看；**禁止 cat 或输出全文** |
| compose 项目 / 研究卷 | `quant-personal` / `quant-personal_personal_var` |
| 上传目录 | `/srv/quant/upload/m1m2c_<SH>`。父目录 `/srv/quant/upload` 属主为 admin（2026-11-20 只读核对） |
| 发布前备份 | `/srv/quant/backup/host-before-<SH>`（`/srv/quant/backup` 为 root 0700） |
| 研究锁（唯一调度者） | `/run/lock/quant-research.lock`。runner、备份、运维共用 |
| Docker | Engine ≥ 26（`volume-subpath`）。服务器为 29.10.0，compose 5.8.0（只读核对） |
| 不动的组件 | feed、news（单列第 11 节）、db、proxy、SEC timer、行情登录与 token、news.env |

**执行方式**：

- 所有 root 步骤都写成 `sudo bash -euo pipefail <<'BLOCK' … BLOCK` 形式的独立块。
- 新起的 root shell 不继承 admin 会话中未 export 的变量。因此每个块第一行都执行：
  ```bash
  . /srv/quant/upload/m1m2c_<SH>/src/deploy/linux/host/m1m2_release_env.sh <SH>
  ```
  这一步重新定义并**校验** `SH D ENV VOL APP NUM U B LOCK C` 这几个变量：
  - 是否为 root；
  - 部署目录、env 文件、研究卷、上传目录是否存在；
  - 已加载镜像的 ID 是否等于清单。

  任何一项不符就返回非 0，整个块随之停止，不执行后续命令。
- 每个块以 `bash -e` 运行，第一条失败的命令即终止整个块。

## 1. 本机准备发布包（不重建镜像）

在候选 checkout 中执行。**先保存仓库路径**：之后进入的任何导出目录都不是 Git 仓库，所以所有 git 命令一律写成 `git -C "$REPO"`。

```bash
REPO=$(git rev-parse --show-toplevel)
RUN=<RELEASE_MANIFEST.runtime.commit>; DEL=<RELEASE_MANIFEST.delivery.commit>
SH=${RUN:0:7}; DSH=${DEL:0:7}
SEALED=<私有目录，含 numeric_$SH.tar.gz、app_$SH.tar.gz 及其 SHA256SUMS>   # 本次：local_only/m1m2c_release
OUT=<新的私有发布目录>; mkdir -p "$OUT"
(cd "$SEALED" && sha256sum -c SHA256SUMS)                                  # 已封存镜像包：只校验，不重建
cp "$SEALED/numeric_$SH.tar.gz" "$SEALED/app_$SH.tar.gz" "$OUT/"
git -C "$REPO" archive --format=tar.gz -o "$OUT/src_$DSH.tar.gz" "$DEL" deploy/linux scripts/quant_research_v1 docs/research_platform
cp <RELEASE_MANIFEST.json> "$OUT/"
(cd "$OUT" && sha256sum numeric_$SH.tar.gz app_$SH.tar.gz src_$DSH.tar.gz RELEASE_MANIFEST.json > SHA256SUMS)
```

- **结果必须与清单一致**：
  - 镜像包的 SHA 必须等于 `RELEASE_MANIFEST.runtime.saved_packages`，否则停止；
  - 源码包内容以清单的逐文件 hash（`delivery.source_files`，第 2 步校验）为准。包本身的 SHA 只用于传输校验（SHA256SUMS），不同 git 版本打出的 tar.gz 字节可能不同。
- **只在必须重建时执行**：
  ```bash
  W=$(mktemp -d); git -C "$REPO" archive "$RUN" | tar -x -C "$W"
  ```
  然后在 `$W` 里按清单记录的命令执行 `docker build`，`$W` 只用于构建。重建通常会得到**不同的镜像 ID**（至少修订标签和时间不同）。此时必须：
  - 生成新的清单；
  - 在新镜像上重做受影响的验收：数值作业定向执行、同配置重跑、隔离恢复。

  不得沿用旧证据，也不得假定 ID 相同。
- 没有推送 registry，所以没有 registry digest。按不可变的本地镜像 ID 加包 SHA 验收。

## 2. 上传与校验（服务器，admin 普通用户）

```bash
# 1) 服务器：建立上传目录（父目录属主 admin；不需要 sudo）
ssh admin@<server> 'install -d -m 0750 /srv/quant/upload/m1m2c_<SH>'
# 2) 本机：只经现有 SSH 上传；包文件在前，SHA256SUMS 最后上传
scp "$OUT"/numeric_<SH>.tar.gz "$OUT"/app_<SH>.tar.gz "$OUT"/src_<DSH>.tar.gz "$OUT"/RELEASE_MANIFEST.json admin@<server>:/srv/quant/upload/m1m2c_<SH>/
scp "$OUT"/SHA256SUMS admin@<server>:/srv/quant/upload/m1m2c_<SH>/
# 3) 服务器（admin）：核对 -> 解包 -> 逐文件核对源码 -> 加载镜像
cd /srv/quant/upload/m1m2c_<SH> && sha256sum -c SHA256SUMS
mkdir -p src && tar -xzf src_<DSH>.tar.gz -C src
python3 -c 'import hashlib,json,pathlib,sys; m=json.load(open("RELEASE_MANIFEST.json")); bad=[f for f,h in m["delivery"]["source_files"].items() if hashlib.sha256(pathlib.Path("src",f).read_bytes()).hexdigest()!=h]; print("source files ok" if not bad else "MISMATCH %s" % bad); sys.exit(1 if bad else 0)'
gunzip -c numeric_<SH>.tar.gz | sudo docker load && gunzip -c app_<SH>.tar.gz | sudo docker load
```

任何一步非 0 都要停止，不进入第 3 步。镜像 ID 由第 3 步起的 release-env 校验与清单比对。

## 3. 发布前备份（只读生产；root 块）

```bash
sudo bash -euo pipefail <<'BLOCK'
. /srv/quant/upload/m1m2c_<SH>/src/deploy/linux/host/m1m2_release_env.sh <SH>
install -d -m 0700 "$B"
cp -a "$ENV" "$D/compose.yaml" "$D/host" /usr/local/sbin/quant-research-runner.sh /usr/local/sbin/quant-restore-check.sh \
  /etc/systemd/system/quant-research-runner.service "$B/"
docker inspect quant-personal-web-1 --format '{{.Config.Image}} {{.Image}}' > "$B/web_image_before.txt"
systemctl start quant-backup.service      # oneshot：等待当日完整备份与恢复检查结束
BLOCK
```

**时间窗**：`quant-backup.timer` 在 04:30（服务器本地时间）运行，会等研究锁最多 15 分钟。发布窗口应避开 04:30–05:15。

## 4–5. 暂停研究链路并切换（单个 root 块，持锁执行；未取得锁即停止）

部署方向不需要暂停入队标志：当前生产 web `0030682` 不认这个标志，但它只能排入 mom20，新旧 worker 都能执行。窗口内由所有者本人不提交新任务。

```bash
sudo systemctl stop quant-research-runner.timer          # 只停研究 timer
sudo bash -euo pipefail <<'BLOCK'
. /srv/quant/upload/m1m2c_<SH>/src/deploy/linux/host/m1m2_release_env.sh <SH>
[ -d "$B" ] || { echo "发布前备份 $B 不存在：停止，未改动任何文件" >&2; exit 1; }
exec 9>"$LOCK"
if ! flock -w 1500 9; then echo "研究锁 25 分钟内未取得：停止切换，未改动任何文件（见本节末尾：安全终止）" >&2; exit 75; fi
# --- 排空核对：没有在跑的作业 ---
[ -z "$(docker ps -q --filter label=com.docker.compose.service=research-runner)" ] || { echo "仍有 legacy 研究容器：停止" >&2; exit 1; }
[ -z "$(docker ps -q --filter label=quant.research.job)" ] || { echo "仍有受保护作业容器：停止" >&2; exit 1; }
R=$(docker run --rm --network none -v "$VOL:/v" --entrypoint python "$NUM" -c "import sqlite3; c=sqlite3.connect('file:/v/research/registry.sqlite3?mode=ro', uri=True); print(c.execute(\"select count(*) from runs where status='RUNNING'\").fetchone()[0])")
[ "$R" = 0 ] || { echo "登记库仍有 $R 个 RUNNING：停止" >&2; exit 1; }
# --- 切换主机文件 ---
install -m 0644 "$U/src/deploy/linux/compose.yaml" "$D/compose.yaml"
install -m 0644 "$U/src/deploy/linux/host/quant_research_backup.py" "$U/src/deploy/linux/host/backup_scope.json" "$D/host/"
install -m 0755 "$U/src/deploy/linux/host/quant-research-runner.sh" "$U/src/deploy/linux/host/quant-research-job.sh" "$U/src/deploy/linux/host/quant-restore-check.sh" /usr/local/sbin/
install -m 0644 "$U/src/deploy/linux/host/quant-research-runner.service" /etc/systemd/system/      # TimeoutStartSec=25min
systemctl daemon-reload
# --- 只改两项镜像名（不输出 env 内容）---
sed -i "s|^APP_IMAGE=.*|APP_IMAGE=$APP|" "$ENV"
if grep -q '^NUMERIC_IMAGE=' "$ENV"; then sed -i "s|^NUMERIC_IMAGE=.*|NUMERIC_IMAGE=$NUM|" "$ENV"; else echo "NUMERIC_IMAGE=$NUM" >> "$ENV"; fi
grep -E '^(APP_IMAGE|NUMERIC_IMAGE)=' "$ENV"
# --- 新 web 在“暂停入队”状态下启动；只重建 web ---
$C --profile jobs run --rm --no-deps -T research-runner admission pause --reason "M1/M2 发布验证中"
$C up -d --no-build --no-deps web
for i in $(seq 1 60); do s=$(docker inspect -f '{{.State.Health.Status}}' quant-personal-web-1); [ "$s" = healthy ] && break; sleep 5; done
[ "$s" = healthy ] || { echo "web 未 healthy：转第 9 节" >&2; exit 1; }
$C --profile jobs run --rm --no-deps -T research-runner register-defaults     # 幂等
echo "switch done; lock released on exit"
BLOCK
```

- **跨服务影响**：`APP_IMAGE` 也被 feed、news、daily-price 引用。只能使用 `up -d --no-deps web`。不带服务名的 `docker compose up -d` 会重建 feed 和 news，不属于本次发布。
- **登记库迁移**：首次连接时只做增量（新增列与表，不改旧行）。
- **锁取不到时（安全终止）**：如果是作业超过上限仍在运行，执行 `sudo systemctl stop quant-research-runner.service`。runner 收到 SIGTERM 后：
  - legacy 作业记为 `FAILED/TERMINATED_BY_SIGNAL`；
  - 受保护作业的 launcher 先停 compute 容器，再做同样登记。

  然后重新执行本块。
- **块失败**：如果在“切换主机文件”之后失败，立刻执行第 9 节。timer 仍处于停止状态，不会自行启动。

## 6. 恢复调度并开放入队

```bash
sudo systemctl start quant-research-runner.timer
sudo journalctl -u quant-research-runner.service -n 40 --no-pager   # 第一次 tick：legacy 空闲；numeric 登记能力，claim 空闲
sudo bash -euo pipefail <<'BLOCK'
. /srv/quant/upload/m1m2c_<SH>/src/deploy/linux/host/m1m2_release_env.sh <SH>
$C --profile jobs run --rm --no-deps -T research-runner admission open
BLOCK
```

经隧道检查：研究页四个入口都能打开，`/research/registry.json` 返回 200 且 `admission_paused` 为 null。私有输入登记之前，phase5 显示“缺少输入”。

## 7. 私有输入登记（需要单独授权；登记前生产保持 BLOCKED_INPUTS）

1. **本机核对**：
   ```bash
   sha256sum <私有目录>/pkg/input/market_research.sqlite
   ```
   结果必须为 `c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e`。
2. **建立目录并上传**：
   ```bash
   ssh admin@<server> 'install -d -m 0700 /srv/quant/upload/phase5_input'
   scp <file> admin@<server>:/srv/quant/upload/phase5_input/market_research.sqlite
   ssh admin@<server> 'sha256sum /srv/quant/upload/phase5_input/market_research.sqlite'
   ```
   最后一步必须得到同一 hash。
3. **登记**：只挂载 research 子路径（读写）和这一个文件（只读），不联网。
   ```bash
   sudo bash -euo pipefail <<'BLOCK'
   . /srv/quant/upload/m1m2c_<SH>/src/deploy/linux/host/m1m2_release_env.sh <SH>
   F=/srv/quant/upload/phase5_input/market_research.sqlite
   [ "$(sha256sum < "$F" | cut -c1-64)" = c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e ] || { echo "hash 不符：停止" >&2; exit 1; }
   chown 10001:10001 "$F"; chmod 0400 "$F"
   docker run --rm --network none --read-only --tmpfs /tmp --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
     --mount type=volume,src="$VOL",dst=/research,volume-subpath=research \
     --mount type=bind,src="$F",dst=/src/market_research.sqlite,readonly \
     -e QUANT_RESEARCH_ROOT=/research "$NUM" register-bundle --source /src/market_research.sqlite \
     --dataset-id phase5_bundle --dataset-version c5b5ec22 --title "phase5 输入快照（私有，c5b5ec22）" \
     --expected-sha256 c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e
   BLOCK
   ```
   - **成功**：输出 `"state": "REGISTERED"`。
   - **重复执行**：输出 `ALREADY_REGISTERED`，不写任何东西。
   - **hash 不符**：`SNAPSHOT_SOURCE_HASH_MISMATCH`，或 `DATASET_VERSION_CONFLICT`。
   - **上次中断留下目录**：`UNREGISTERED_DIRECTORY_EXISTS`，需先人工检查该目录。
   - 以上失败都返回非 0，且不改变登记库。
4. **可选：参考账本**（私有，不进包）：
   ```bash
   ... --mount type=bind,src=<private_run>,dst=/ref,readonly "$NUM" register-reference --source /ref --input-sha c5b5ec22...
   ```
   330 个文件逐个核对冻结索引；同一套已登记时为 `ALREADY_REGISTERED`。
5. **暂存副本**：登记成功后，上传暂存副本是否删除由所有者决定（卷内已有逐字节副本）。

## 8. 发布后小范围验收

1. 所有者从页面提交一次 phase5，下一次 tick 应完成，并满足：
   - results digest = `1783db80eb33f48fd9b84233491f986606655adf66e1b0f5c32de8ac3671ca06`；
   - 机械复核三层 PASS，330/330 原始 hash 一致；
   - 收益筛选仍为“未通过（0/12）”。
2. 下载 3 个产物，核对 `X-Content-SHA256`；再“用相同冻结配置重跑”一次。
3. 次日检查备份：`RESTORE_CHECK.json` 中 `research.state` 应为 `OK`。phase5 重执行经由 `quant-research-job.sh --var-dir` 运行，每个 v2 运行约 2 分钟。
4. 服务器 timer 是否自然触发、主机重启后是否正常，都只按真实观察记录；没有观察到的不写成功。

## 9. 发布过程中失败（第 6 步之前；root 块，持锁）

```bash
sudo bash -euo pipefail <<'BLOCK'
. /srv/quant/upload/m1m2c_<SH>/src/deploy/linux/host/m1m2_release_env.sh <SH>
[ -d "$B" ] || { echo "备份 $B 不存在：停止，人工处理" >&2; exit 1; }
exec 9>"$LOCK"; flock -w 1500 9 || { echo "研究锁未取得：停止" >&2; exit 75; }
install -m 0600 "$B/private.env" "$ENV"
cp -a "$B/compose.yaml" "$D/compose.yaml"; cp -a "$B/host/." "$D/host/"
install -m 0755 "$B/quant-research-runner.sh" "$B/quant-restore-check.sh" /usr/local/sbin/
rm -f /usr/local/sbin/quant-research-job.sh
install -m 0644 "$B/quant-research-runner.service" /etc/systemd/system/; systemctl daemon-reload
$C up -d --no-build --no-deps web
rm -f "/var/lib/docker/volumes/$VOL/_data/research/ADMISSION_PAUSED"
BLOCK
sudo systemctl start quant-research-runner.timer
```

登记库只做了增量迁移，旧版本读取正常（演练 E3：`0030682` 的 web 读取新登记库返回 200），无需回退数据。

## 10. 发布后回滚（root 块，持锁；未取得锁即停止）

### 10.1 一级：只停用数值作业（web 和 legacy 保持候选版本）

```bash
sudo bash -euo pipefail <<'BLOCK'
. /srv/quant/upload/m1m2c_<SH>/src/deploy/linux/host/m1m2_release_env.sh <SH>
$C --profile jobs run --rm --no-deps -T research-runner admission pause --reason "停用数值作业"
systemctl stop quant-research-runner.timer
exec 9>"$LOCK"; flock -w 1500 9 || { echo "研究锁未取得：停止（timer 已停，入队已暂停）" >&2; exit 75; }
$C --profile jobs run --rm --no-deps -T research-runner withdraw-capability --engine phase5_o2o_v1@1
sed -i '/^NUMERIC_IMAGE=/d' "$ENV"
flock -u 9
systemctl start quant-research-runner.timer
$C --profile jobs run --rm --no-deps -T research-runner admission open
BLOCK
```

`withdraw-capability` 之后，页面恢复为“执行引擎镜像尚未构建登记”。排队中的 phase5 保持 QUEUED，不丢失，候选 legacy worker 也不会领取。

### 10.2 二级：全量回滚到 `quant-personal-app:0030682`

```bash
sudo bash -euo pipefail <<'BLOCK'
. /srv/quant/upload/m1m2c_<SH>/src/deploy/linux/host/m1m2_release_env.sh <SH>
[ -d "$B" ] || { echo "备份 $B 不存在：停止" >&2; exit 1; }
$C --profile jobs run --rm --no-deps -T research-runner admission pause --reason "回滚到 0030682"
systemctl stop quant-research-runner.timer
exec 9>"$LOCK"; flock -w 1500 9 || { echo "研究锁未取得：停止（timer 已停，入队已暂停）" >&2; exit 75; }
# 必需：0030682 的 worker 没有 engine 过滤，会领取最早的 QUEUED 运行，并让 phase5 失败（演练 E3：KeyError）
$C --profile jobs run --rm --no-deps -T research-runner cancel-queued --engine phase5_o2o_v1@1 --reason "rollback to 0030682 (no engine dispatch)"
sed -i "s|^APP_IMAGE=.*|APP_IMAGE=quant-personal-app:0030682|" "$ENV"; sed -i '/^NUMERIC_IMAGE=/d' "$ENV"
cp -a "$B/compose.yaml" "$D/compose.yaml"; cp -a "$B/host/." "$D/host/"
install -m 0755 "$B/quant-research-runner.sh" "$B/quant-restore-check.sh" /usr/local/sbin/; rm -f /usr/local/sbin/quant-research-job.sh
install -m 0644 "$B/quant-research-runner.service" /etc/systemd/system/; systemctl daemon-reload
docker compose -f "$D/compose.yaml" --env-file "$ENV" up -d --no-build --no-deps web
rm -f "/var/lib/docker/volumes/$VOL/_data/research/ADMISSION_PAUSED"
flock -u 9
systemctl start quant-research-runner.timer
BLOCK
```

回滚后核对：web 镜像为 `0030682`、研究页 200、第一次 tick 日志正常。

数据：v2 表和列、产物、参考账本、数据集全部保留（增量），不删除。将来再次发布候选版本时可以直接读到。

## 11. 新闻 collector 升级（单独决定；本轮不执行）

新闻修复（每通道完成水位与续传、旧删除不覆盖较新内容、固定副本恢复对账）在 news 容器换成候选镜像并重启后才生效。

1. **记录现状**：`docker inspect quant-personal-news-1 --format '{{.Config.Image}}'`，以及 news.json 的当前状态（只记录状态，不输出密钥）。
2. **固定副本备份**：用 `scripts.quant_news_v1.backup_check digest` 生成固定副本的摘要。
3. **重建 news**：`$C --profile news up -d --no-build --no-deps news`。news.env 与密钥不动，也不要求重新输入。
4. **小流量检查**：
   - 连接后，REST 补漏应按 `articles`/`removed` 两个通道推进完成水位；
   - `news.json` 中的 `receipt` 和 `gap_fill` 正常；
   - 遇到 429 停止，不循环重试；
   - 在线状态与 `historical_news_panel_ready=false` 分开显示。
5. **回滚**：news 与 web 共用 `APP_IMAGE`。单独回滚 news 时，临时把 `APP_IMAGE` 改回 news 原镜像，执行 `$C --profile news up -d --no-build --no-deps news`，再改回候选（web 未被重建，不受影响）。新增的 cursor 键对旧代码无害。

## 12. 演练证据（本机隔离环境，镜像 6fe5001）

环境：测试卷 `m1m2f_personal_var`、只含假值的测试 env 文件。辅助主机是 Linux 容器（bash、flock、Docker Desktop 自带的 Docker CLI 29.6.2、compose 5.3.1），原样运行候选的 runner 和 launcher。

| 演练 | 结果 |
|---|---|
| 空闲 tick（runner） | 登记能力 |
| 网页入队 → runner tick → 受保护作业 | SUCCEEDED，digest 1783db80…；机械三层 PASS；330/330 一致 |
| 定向重跑 + 隔离探针 | 19 项禁止操作全部被内核拒绝；输入 hash、另一 run 目录、哨兵均不变 |
| 取消 / 超时 / SIGTERM / 崩溃 | CANCELLED_WHILE_RUNNING / TIMEOUT / TERMINATED_BY_SIGNAL / WORKER_LOST（租约过期后）；作业容器为 0 |
| 备份 → 定向恢复（`--var-dir`）→ verify-finish | OK；9/9 产物一致；原记录不变；缺件为 FAILED_SCOPE_CHECK |
| 持锁 / 暂停入队 / 一级与二级回滚 / 再发布 | runner 在运维持锁时退出；入队请求 503；ENGINE_NOT_BUILT；`cancel-queued` 后旧 web 200、旧 worker 无 phase5 可领；再发布正常 |

**未演练**：服务器真实 systemd 自然触发、主机重启、跨机器镜像传输、生产执行。

## 13. 手册命令 smoke（临时目录 / 临时 shell，非生产）

见发布包 `runbook_smoke/`，记录了每条命令的真实退出码：

- **源码包导出**：
  - 旧写法在导出目录内 `git archive`：exit 128（not a git repository）；
  - 改为 `git -C "$REPO"`：exit 0，且包内容与清单一致。
- **上传顺序**：建目录 → 复制 → `sha256sum -c` → 解包 → 逐文件核对：通过。篡改一个包后 `sha256sum -c` 非 0。
- **root 块**：
  - 非 root 时 release-env 返回 1；
  - SH 格式错误返回 1；
  - 镜像 ID 与清单不符返回 1；
  - 全部正确时返回 0 并输出变量。
- **持锁**：
  - 另一进程持有研究锁时，切换块以 exit 75 停止，标记文件没有生成（未执行任何安装步骤）；
  - 锁空闲时继续执行。
