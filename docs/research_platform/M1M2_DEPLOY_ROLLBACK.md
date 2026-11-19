# M1/M2 发布与回滚手册（候选，未在生产执行）

版本：closeout 2026-11-20。分支 `codex/m1m2-closeout-20261120`（在 `a55e261` 之上）。

- **生产执行条件**：本手册的生产步骤只在获得明确发布指令后执行，本轮没有改动生产。
- **准确值来源**：候选的精确值（提交、镜像 ID、修订标签、锁文件 hash、源码清单）以候选包里的 `RELEASE_MANIFEST.json` 为准。下文用变量引用这些值，不在此处手抄。
- **已演练范围**：本手册的启动、回滚与恢复路径，已在本机隔离环境用候选脚本原样演练（见第 12 节）。
- **未覆盖范围**：主机重启演练、服务器上真实的 systemd timer、跨机器的镜像传输都没有做过。

## 0. 固定值与约束

| 项 | 值 |
|---|---|
| 访问方式 | 只用现有 SSH（admin + sudo）；不开放 80/443/8443 或数据库端口 |
| 部署目录 | `/srv/quant/app/deploy/linux`（compose.yaml 所在，不是 git 检出） |
| 私有环境文件 | `/srv/quant/private.env`（root 0600）。只用 `sed`/`grep -E '^(APP_IMAGE\|NUMERIC_IMAGE)='` 处理两项镜像名，**禁止 cat 或输出全文** |
| compose 项目 / 研究卷 | `quant-personal` / `quant-personal_personal_var` |
| 主机脚本安装位置 | `/usr/local/sbin/`；systemd 单元在 `/etc/systemd/system/` |
| 研究锁（唯一调度者） | `/run/lock/quant-research.lock`。runner、备份、运维人员共用这一把锁 |
| Docker 要求 | Engine ≥ 26（`volume-subpath`）。2026-11-20 只读核对：服务器为 29.10.0（API 1.57），compose 5.8.0 |
| 不动的组件 | feed、news、db、proxy、SEC timer、行情登录与 token、news.env |

服务器上的通用变量：

```bash
D=/srv/quant/app/deploy/linux; ENV=/srv/quant/private.env; VOL=quant-personal_personal_var
C="docker compose -f $D/compose.yaml --env-file $ENV"
SH=<RELEASE_MANIFEST.short>; APP=quant-personal-app:$SH; NUM=quant-research-numeric:$SH
U=/srv/quant/upload/m1m2c_$SH; B=/srv/quant/backup/host-before-$SH
```

## 1. 本机构建与封存（从候选提交的 git archive）

```bash
SHA=<RELEASE_MANIFEST.commit>; SH=${SHA:0:7}; W=$(mktemp -d); git archive $SHA | tar -x -C $W; cd $W
LOCK=$(sha256sum < deploy/linux/requirements-numeric.lock | cut -c1-64)   # 必须等于 RELEASE_MANIFEST.lockfile_sha256
docker build -f deploy/linux/Dockerfile.numeric --build-arg LOCK_SHA256=$LOCK --label org.opencontainers.image.revision=$SHA -t quant-research-numeric:$SH .
docker build -f deploy/linux/Dockerfile --label org.opencontainers.image.revision=$SHA -t quant-personal-app:$SH .
docker image inspect quant-research-numeric:$SH quant-personal-app:$SH --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker save quant-research-numeric:$SH | gzip -n > numeric_$SH.tar.gz; docker save quant-personal-app:$SH | gzip -n > app_$SH.tar.gz
git archive --format=tar.gz -o src_$SH.tar.gz $SHA deploy/linux scripts/quant_research_v1
sha256sum numeric_$SH.tar.gz app_$SH.tar.gz src_$SH.tar.gz > SHA256SUMS
```

镜像 ID 与修订标签必须同时等于 `RELEASE_MANIFEST.images`，否则停止。没有推送 registry，所以没有 registry digest；按不可变的本地镜像 ID 加包 SHA 验收。

## 2. 传输与校验（服务器）

```bash
# 本机执行：经现有 SSH 上传
scp numeric_$SH.tar.gz app_$SH.tar.gz src_$SH.tar.gz SHA256SUMS admin@<server>:/srv/quant/upload/m1m2c_$SH/
# 服务器执行
cd $U && sha256sum -c SHA256SUMS
gunzip -c numeric_$SH.tar.gz | sudo docker load; gunzip -c app_$SH.tar.gz | sudo docker load
sudo docker image inspect $NUM $APP --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'   # 与清单一致
mkdir -p src && tar -xzf src_$SH.tar.gz -C src
sha256sum src/deploy/linux/host/quant-research-job.sh src/deploy/linux/host/quant-research-runner.sh src/deploy/linux/compose.yaml   # 与 RELEASE_MANIFEST.source_files 一致
```

## 3. 发布前备份（只读取生产）

```bash
sudo install -d -m 0700 $B
sudo cp -a $ENV $D/compose.yaml $D/host /usr/local/sbin/quant-research-runner.sh /usr/local/sbin/quant-restore-check.sh \
  /etc/systemd/system/quant-research-runner.service $B/
sudo docker inspect quant-personal-web-1 --format '{{.Config.Image}} {{.Image}}' | sudo tee $B/web_image_before.txt
sudo systemctl start quant-backup.service    # 当日完整备份（含恢复检查），耗时随研究运行数增加
```

**时间窗**：`quant-backup.timer` 在 04:30（服务器本地时间）运行，会等研究锁最多 15 分钟。发布窗口应避开 04:30–05:15。

## 4. 暂停研究链路（只针对研究；行情/新闻/SEC 不停）

1. **暂停新入队**：
   - 当前生产 web `0030682` 不认暂停标志，但它只能排入 mom20（新旧 worker 都能执行），所以部署方向没有风险；
   - 窗口内所有者不提交新任务；
   - 候选 web 认 `research/ADMISSION_PAUSED`（见第 5 步）。
2. **停研究 timer**：
   ```bash
   sudo systemctl stop quant-research-runner.timer
   ```
3. **用同一把 flock 排空**。在运维用的 root shell 中执行，并在整个切换期间保持这个 shell：
   ```bash
   sudo -s
   exec 9>/run/lock/quant-research.lock; flock -w 1500 9 && echo "research lock held"
   ```
   如果等待超时（作业超过上限仍在跑），执行安全终止，然后重新取锁：
   ```bash
   systemctl stop quant-research-runner.service
   ```
   - 旧 runner 收到 SIGTERM 后，作业记为 `FAILED/TERMINATED_BY_SIGNAL`，部分文件保留；
   - 新 runner 的 launcher 会先停 compute 容器，再做同样的登记。
4. **确认没有在跑的作业**：
   ```bash
   docker ps --filter label=com.docker.compose.service=research-runner --format '{{.Names}}'   # 应为空
   docker ps --filter label=quant.research.job --format '{{.Names}}'                           # 应为空
   docker run --rm --network none -v $VOL:/v:ro --entrypoint python $(sed -n 's/^APP_IMAGE=//p' $ENV) -c \
     "import sqlite3; c=sqlite3.connect('file:/v/research/registry.sqlite3?mode=ro&immutable=1', uri=True); print(c.execute('select status, count(*) from runs group by 1').fetchall())"
   ```
   结果中不应有 RUNNING。

## 5. 切换组件（仍持有研究锁）

1. **安装主机文件**：
   ```bash
   install -m 0644 $U/src/deploy/linux/compose.yaml $D/compose.yaml
   install -m 0644 $U/src/deploy/linux/host/quant_research_backup.py $U/src/deploy/linux/host/backup_scope.json $D/host/
   install -m 0755 $U/src/deploy/linux/host/quant-research-runner.sh $U/src/deploy/linux/host/quant-research-job.sh \
     $U/src/deploy/linux/host/quant-restore-check.sh /usr/local/sbin/
   install -m 0644 $U/src/deploy/linux/host/quant-research-runner.service /etc/systemd/system/   # TimeoutStartSec=25min
   systemctl daemon-reload
   ```
2. **只改两项镜像名，不输出文件内容**：
   ```bash
   sed -i "s|^APP_IMAGE=.*|APP_IMAGE=$APP|" $ENV
   grep -q '^NUMERIC_IMAGE=' $ENV && sed -i "s|^NUMERIC_IMAGE=.*|NUMERIC_IMAGE=$NUM|" $ENV || echo "NUMERIC_IMAGE=$NUM" >> $ENV
   grep -E '^(APP_IMAGE|NUMERIC_IMAGE)=' $ENV
   ```
3. **新 web 在暂停状态下启动**：
   ```bash
   $C --profile jobs run --rm --no-deps -T research-runner admission pause --reason "M1/M2 发布验证中"
   $C up -d --no-build --no-deps web
   docker inspect quant-personal-web-1 --format '{{.Config.Image}} {{.State.Health.Status}}'
   $C --profile jobs run --rm --no-deps -T research-runner register-defaults   # 幂等：v2 规格与 phase5 历史导入
   ```
   - 首次连接时，登记库只做增量迁移（新增列与表，不改旧行）。
   - **注意**：`APP_IMAGE` 也被 feed/news/daily-price 引用。只能使用 `up -d --no-deps web`。不带服务名的 `docker compose up -d` 会用新镜像重建 feed 和 news，这不属于本次发布。
4. **经隧道检查页面**：研究页四个入口都能打开；`/research/registry.json` 返回 200，且 `admission_paused` 不为空。导入私有输入之前，phase5 应显示“缺少输入 / 执行引擎镜像尚未构建登记”。

## 6. 恢复调度

```bash
exec 9>&-                                  # 释放研究锁
systemctl start quant-research-runner.timer
journalctl -u quant-research-runner.service -n 40 --no-pager   # 第一次 tick：legacy 空闲；numeric 记录能力，claim 空闲
$C --profile jobs run --rm --no-deps -T research-runner admission open
```

## 7. 私有输入登记（需要单独授权；登记前生产保持 BLOCKED_INPUTS）

1. **本机核对**：
   ```bash
   sha256sum <私有目录>/pkg/input/market_research.sqlite
   ```
   结果必须为 `c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e`。
2. **最小范围传输**。所有者用现有 SSH 上传单个文件：
   ```bash
   scp <file> admin@<server>:/srv/quant/upload/phase5_input/market_research.sqlite
   ```
   服务器上核对：
   ```bash
   sha256sum /srv/quant/upload/phase5_input/market_research.sqlite      # 同一 hash
   sudo chown 10001:10001 /srv/quant/upload/phase5_input/market_research.sqlite; sudo chmod 0400 /srv/quant/upload/phase5_input/market_research.sqlite
   ```
3. **登记**。只挂载 research 子路径（读写）和这一个文件（只读），不联网：
   ```bash
   sudo docker run --rm --network none --read-only --tmpfs /tmp --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
     --mount type=volume,src=$VOL,dst=/research,volume-subpath=research \
     --mount type=bind,src=/srv/quant/upload/phase5_input/market_research.sqlite,dst=/src/market_research.sqlite,readonly \
     -e QUANT_RESEARCH_ROOT=/research $NUM register-bundle --source /src/market_research.sqlite \
     --dataset-id phase5_bundle --dataset-version c5b5ec22 --title "phase5 输入快照（私有，c5b5ec22）" \
     --expected-sha256 c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e
   ```
   - **成功**：输出 `"state": "REGISTERED"`。
   - **重复执行**：输出 `ALREADY_REGISTERED`，不写任何东西。
   - **hash 不符**：`SNAPSHOT_SOURCE_HASH_MISMATCH`，或 `DATASET_VERSION_CONFLICT`。
   - **上次中断留下目录**：`UNREGISTERED_DIRECTORY_EXISTS`，需要先人工检查该目录。
   - 以上失败都返回非 0，且不改变登记库。
4. **可选：登记参考账本**。只有原始 hash 出现不一致、需要逐行对账时才用得到：
   ```bash
   ... --mount type=bind,src=<private_run>,dst=/ref,readonly $NUM register-reference --source /ref --input-sha c5b5ec22...
   ```
   330 个文件逐个核对冻结索引；同一套已登记时输出 `ALREADY_REGISTERED`。私有文件，不公开、不进包。
5. **暂存副本**：登记成功后，上传暂存副本是否删除由所有者决定（卷内已有逐字节副本）。

## 8. 发布后小范围验收

1. 所有者从页面提交一次 phase5，下一次 tick 应完成，并满足：
   - results digest = `1783db80eb33f48fd9b84233491f986606655adf66e1b0f5c32de8ac3671ca06`；
   - 机械复核三层 PASS，330/330 原始 hash 一致；
   - 收益筛选仍为“未通过（0/12）”。
2. 下载 3 个产物，核对 `X-Content-SHA256`；再“用相同冻结配置重跑”一次。
3. 次日检查备份：`RESTORE_CHECK.json` 中 `research.state` 应为 `OK`。phase5 重执行经由 `quant-research-job.sh --var-dir` 运行，每个 v2 运行约 2 分钟。

## 9. 发布过程中失败

在第 6 步之前出错时：

```bash
install -m 0600 $B/private.env $ENV; cp -a $B/compose.yaml $D/compose.yaml; cp -a $B/host/. $D/host/
install -m 0755 $B/quant-research-runner.sh $B/quant-restore-check.sh /usr/local/sbin/; rm -f /usr/local/sbin/quant-research-job.sh
install -m 0644 $B/quant-research-runner.service /etc/systemd/system/; systemctl daemon-reload
$C up -d --no-build --no-deps web
rm -f /var/lib/docker/volumes/$VOL/_data/research/ADMISSION_PAUSED
exec 9>&-; systemctl start quant-research-runner.timer
```

登记库只做了增量迁移，旧版本读取正常（演练 E3：`0030682` 的 web 读取新登记库返回 200），无需回退数据。

## 10. 发布后回滚

### 10.1 一级：只停用数值作业（web 和 legacy 保持候选版本）

1. `$C --profile jobs run --rm --no-deps -T research-runner admission pause --reason "停用数值作业"`
2. `systemctl stop quant-research-runner.timer`，然后按第 4 步取锁并排空。
3. `$C --profile jobs run --rm --no-deps -T research-runner withdraw-capability --engine phase5_o2o_v1@1`：页面恢复为“执行引擎镜像尚未构建登记”。排队中的 phase5 保持 QUEUED，不丢失，候选 legacy worker 也不会领取。
4. `sed -i '/^NUMERIC_IMAGE=/d' $ENV`
5. `exec 9>&-; systemctl start quant-research-runner.timer`，然后执行 `admission open`。

### 10.2 二级：全量回滚到 `quant-personal-app:0030682`

1. **暂停入队**（候选 web 遵守标志）：`$C --profile jobs run --rm --no-deps -T research-runner admission pause --reason "回滚到 0030682"`。
2. 停 timer，按第 4 步取锁排空。
3. **必须先清掉排队中的 v2 运行**：
   ```bash
   $C --profile jobs run --rm --no-deps -T research-runner cancel-queued --engine phase5_o2o_v1@1 --reason "rollback to 0030682 (no engine dispatch)"
   ```
   - **原因**：`0030682` 的 worker 没有 engine 过滤，会领取最早的 QUEUED 运行。演练 E3 中，一个故意不清理的 phase5 运行因此变成 `FAILED/KeyError`；先取消的那个保持 `CANCELLED_BEFORE_START`。
4. **改回镜像配置**：
   ```bash
   sed -i "s|^APP_IMAGE=.*|APP_IMAGE=quant-personal-app:0030682|" $ENV; sed -i '/^NUMERIC_IMAGE=/d' $ENV
   ```
5. **恢复主机文件与单元**：同第 9 步（runner、restore-check、backup 脚本、scope、compose、service，以及 daemon-reload）。
6. **重建 web 并清理标志**：
   ```bash
   $C up -d --no-build --no-deps web; rm -f /var/lib/docker/volumes/$VOL/_data/research/ADMISSION_PAUSED
   ```
7. **恢复调度并核对**：
   ```bash
   exec 9>&-; systemctl start quant-research-runner.timer
   ```
   核对 web 镜像为 `0030682`、研究页 200、第一次 tick 日志。

数据：v2 表和列、产物、参考账本、数据集全部保留（增量），不删除。将来再次发布候选版本时可以直接读到。

## 11. 新闻 collector 升级（单独决定；本轮不执行）

新闻修复（每通道完成水位与续传、旧删除不覆盖较新内容、固定副本恢复对账）在 news 容器换成候选镜像并重启后才生效。步骤：

1. **记录现状**：`docker inspect quant-personal-news-1 --format '{{.Config.Image}}'`，以及 news.json 的当前状态（只记录状态，不输出密钥）。
2. **固定副本备份**：
   ```bash
   docker run --rm --network none --user 10001:10001 --entrypoint python -v $VOL:/src:ro -v <备份目录>:/out $APP -m scripts.quant_news_v1.backup_check digest /src/news/<cache>.sqlite
   ```
3. **重建 news**：`$C --profile news up -d --no-build --no-deps news`。news.env 与密钥不动，也不要求重新输入。
4. **小流量检查**：
   - 连接后，REST 补漏应按 `articles`/`removed` 两个通道推进完成水位；
   - `news.json` 中的 `receipt` 和 `gap_fill` 正常；
   - 遇到 429 停止，不循环重试；
   - 在线状态与 `historical_news_panel_ready=false` 分开显示。
5. **回滚**：news 与 web 共用 `APP_IMAGE`。单独回滚 news 时，临时把 `APP_IMAGE` 改回 news 原镜像，执行 `$C --profile news up -d --no-build --no-deps news`，再把 `APP_IMAGE` 改回候选（web 未被重建，不受影响）。新增的 cursor 键对旧代码无害。

## 12. 演练证据与未演练项

全部为本机隔离环境的结果：测试卷 `m1m2c_personal_var`、测试 env 文件（只含假值）。辅助主机是 Linux 容器（bash、flock、Docker Desktop 自带的 Docker CLI 29.6.2、compose 5.3.1），原样运行候选的 runner 和 launcher。

| 演练 | 结果 | 证据 |
|---|---|---|
| 空闲 tick（runner） | 登记能力，exit 0 | loop.log A2 |
| 网页入队 → runner tick → 受保护作业 | SUCCEEDED，digest 1783db80…；机械三层 PASS；330/330 一致 | A3/A4 |
| 定向重跑 + 隔离探针 | 19 项禁止操作全部被内核拒绝（EROFS/EXDEV）；输入 hash 不变；另一 run 目录不变；哨兵不变 | B3 |
| 取消 / 超时 / SIGTERM / 崩溃 | CANCELLED_WHILE_RUNNING / TIMEOUT / TERMINATED_BY_SIGNAL / WORKER_LOST（租约过期后）；作业容器 0；哨兵不变 | C1–C4 |
| 备份 → 定向恢复（`--var-dir` 受保护重执行）→ verify-finish | OK；9/9 确定性产物逐字节一致；原记录（含哨兵）不变；缺件 → FAILED_SCOPE_CHECK | D1–D3 |
| 研究锁排他 / 发布与回滚到 0030682 / 再发布 | runner 在运维持锁时退出；旧 web 读新登记库 200；未清理的 phase5 → KeyError（证明第 10.2 步第 3 项是必需的）；再发布正常 | E1–E4 |

**未演练**：
- 服务器上的真实 systemd timer 与主机重启（留到维护窗口）；
- 跨机器镜像传输；
- 生产执行。
