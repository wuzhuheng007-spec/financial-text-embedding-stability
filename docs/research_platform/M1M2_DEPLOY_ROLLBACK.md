# M1/M2 发布与回滚手册（脚本版）

版本：2026-11-20 运维收尾。所有 root 操作都是 `deploy/linux/host/release/` 下的版本化脚本。精确版本以发布目录中的 `RELEASE_MANIFEST.json`（schema v3）为准，本文用 `<…>` 引用。

## 版本对应（当前生产）

| 部分 | 清单字段 | 当前值 |
|---|---|---|
| web 镜像 | `runtime.images.app` | `quant-personal-app:2f8b20f`（6fe5001 加英文显示修复，只改静态文件和模板） |
| 数值作业镜像 | `runtime.images.numeric` | `quant-research-numeric:6fe5001` |
| 主机脚本、手册 | `delivery.commit` | 本文所在提交；逐文件 hash 见 `delivery.source_files` |
| 回滚点 | `production.pre_release_backup` | `/srv/quant/backup/host-before-6fe5001`（M1/M2 之前的主机文件） |
| 回滚目标 web | `production.pre_release_app_image` | `quant-personal-app:0030682` |

## 0. 执行方式（E-1 之后的唯一写法）

```bash
sudo bash -euo pipefail <RELDIR>/src/deploy/linux/host/release/<脚本>.sh [参数] </dev/null
```

`<RELDIR>` 是服务器上的发布目录：`/srv/quant/upload/m1m2r_<release_id>`。里面有：

- `RELEASE_MANIFEST.json`
- `src/`：`delivery.commit` 的 git archive

**禁止**把脚本内容贴进 `sudo bash <<'BLOCK'` 或用管道喂给 bash。

**E-1 的经过**（2026-11-20 08:58 生产发生）：旧手册的 root 块经 stdin 交给 bash。块内第一条 `docker compose run` 继承了这个 stdin，把剩余命令当作自己的输入读走；bash 读到 EOF，以 0 退出，后续步骤都没有执行。

脚本中的防护：

1. 只接受从文件执行（`BASH_SOURCE` 检查）。通过 stdin、heredoc 或管道执行时退出 64，不做任何改动。
2. 必须带 `-e -u -o pipefail`，必须以 root 运行，否则退出 64。
3. 脚本开头 `exec </dev/null`；每条容器命令另外再接 `</dev/null`。
4. 发布目录中 `delivery.source_files` 列出的每个文件都要与 hash 一致，包括脚本自身和 `m1m2_env.sh`，否则退出 1。
5. 已加载镜像的 ID 必须等于清单记录，否则退出 1。
6. 输出约定：
   - 每一步打印 `== STEP k/N … : OK`；
   - 完整结束时最后一行是 `M1M2_COMPLETE script=… release=… steps=N`；
   - 中途停止时打印 `M1M2_STOP … code=… reason=…` 和 `M1M2_INCOMPLETE … last_step=k/N`。
7. 每次执行同时追加到 `/srv/quant/backup/m1m2_ops_journal.log`（root 0700 目录），记录 START、每个 STEP、COMPLETE 或 STOP。

**退出码**：0 表示完成；1 表示核对失败并停止；64 表示调用方式不对；75 表示研究锁未取得。

**判断成功**：退出码为 0，**并且**最后一行是 `M1M2_COMPLETE`，二者缺一不可。

**不动的组件**：feed、news（单列第 11 节）、db、proxy、SEC timer、行情登录与 token、news.env。只用现有 SSH，不开放 80、443、8443 或数据库端口。

| 脚本 | 手册节 | 步数 | 作用 |
|---|---|---|---|
| `m1m2_30_prerelease_backup.sh` | 3 | 4 | 建立回滚点并做一次完整备份和恢复检查。**回滚点已存在时拒绝执行**，不覆盖 |
| `m1m2_45_switch.sh` | 4–5 | 9 | 停研究 timer → 持锁排空 → 安装主机文件 → 写两行镜像 → 暂停入队 → 只重建 web → 登记默认项。timer 保持停止 |
| `m1m2_60_resume.sh` | 6 | 5 | 启动 timer → 等到它触发的第一次 tick 成功结束 → 核对数值能力 → 开放入队 |
| `m1m2_70_register_inputs.sh` | 7 | 3 | 登记私有快照，可选登记参考账本。最小挂载，幂等，冲突时拒绝 |
| `m1m2_101_rollback_numeric.sh` | 10.1 | 7 | 一级回滚：只停用数值作业 |
| `m1m2_102_rollback_full.sh` | 9 / 10.2 | 10 | 全量回滚到发布前的 web 与主机文件。先取消排队中的 v2 任务 |
| `make_manifest.py` | 1 | — | 在本机生成清单 |

## 1. 本机准备（不重建镜像）

```bash
REPO=$(git rev-parse --show-toplevel); DEL=<delivery commit>; OUT=<新的私有发布目录>; mkdir -p "$OUT"
cp <已封存的镜像包> "$OUT/"                                    # app_<sh>.tar.gz、numeric_<sh>.tar.gz：只校验，不重建
git -C "$REPO" archive --format=tar.gz -o "$OUT/src_${DEL:0:7}.tar.gz" "$DEL" deploy/linux scripts/quant_research_v1 docs/research_platform
python "$REPO/deploy/linux/host/release/make_manifest.py" --commit "$DEL" --out "$OUT/RELEASE_MANIFEST.json" \
  --app <app tag> --app-package "$OUT/app_<sh>.tar.gz" --numeric <numeric tag> --numeric-package "$OUT/numeric_<sh>.tar.gz" \
  --pre-release-backup /srv/quant/backup/host-before-6fe5001 --pre-release-app quant-personal-app:0030682
(cd "$OUT" && sha256sum *.tar.gz RELEASE_MANIFEST.json > SHA256SUMS)
```

- 镜像包的 SHA 必须等于清单中的 `package_sha256`。
- 必须重建镜像时：
  - 必须用与原镜像相同的构建目标（app 镜像为默认目标 `research`，**不是** `--target app`）；
  - 层比较后，只允许最后的 `COPY /app` 层不同；
  - 再做受影响的验收。

## 2. 上传与校验（admin）

```bash
ssh admin@<server> 'install -d -m 0750 /srv/quant/upload/m1m2r_<release_id>'
scp "$OUT"/*.tar.gz "$OUT"/RELEASE_MANIFEST.json admin@<server>:/srv/quant/upload/m1m2r_<release_id>/   # 包在前
scp "$OUT"/SHA256SUMS admin@<server>:/srv/quant/upload/m1m2r_<release_id>/                             # 校验文件最后传
# 服务器上（admin）：
cd /srv/quant/upload/m1m2r_<release_id> && sha256sum -c SHA256SUMS && mkdir -p src && tar -xzf src_<release_id>.tar.gz -C src
gunzip -c <app 包> | sudo docker load; gunzip -c <numeric 包> | sudo docker load    # 服务器上已有相同 ID 的镜像时可跳过
```

不需要单独核对源码：每个脚本启动时都会把 `src/` 与清单逐文件比较，再比较镜像 ID。

## 3. 发布前备份

```bash
sudo bash -euo pipefail /srv/quant/upload/m1m2r_<id>/src/deploy/linux/host/release/m1m2_30_prerelease_backup.sh </dev/null
```

**前提**：回滚点目录还不存在；运行中的 web 必须是清单中的发布前镜像。

**做什么**：复制 env、compose、host 目录、runner、restore-check、backup 脚本以及四个单元文件，记录 web 镜像与登记库 hash，生成 `SHA256SUMS`；再运行 `quant-backup.service`，要求最新的 `RESTORE_CHECK.json` 为 ok。

**时间窗**：避开 04:30–05:15（备份 timer）。

**当前生产**：`host-before-6fe5001` 已经存在，本脚本会拒绝执行，这是预期结果。旧第 3 节的块在这种情况下会覆盖回滚点。

## 4–5. 切换

```bash
sudo bash -euo pipefail /srv/quant/upload/m1m2r_<id>/src/deploy/linux/host/release/m1m2_45_switch.sh </dev/null
```

**步骤**：

1. 回滚点完整；
2. 只停研究 timer；
3. 取研究锁（最多等 25 分钟）；
4. 排空核对：没有 legacy 或受保护作业容器，RUNNING=0；
5. 安装 7 个主机文件，并逐个与清单比较，然后 daemon-reload；
6. 写两行镜像名；
7. 暂停入队；
8. 只重建 web，等到 healthy，并核对它运行的镜像 ID；
9. `register-defaults`。

**跨服务影响**：`APP_IMAGE` 也被 feed、news、daily-price 和 legacy 研究 worker 引用。脚本只执行 `up -d --no-deps web`。

**失败时**：

- 退出 75：研究锁未取得，主机文件没有被改动，timer 处于停止状态。如果是作业超时还在运行，先 `sudo systemctl stop quant-research-runner.service`，再重新执行本脚本。
- 第 5 步之后失败：执行第 9 节。

**只换 web 镜像时**：生成新清单（新的 app tag），再依次执行本脚本和第 6 节。2f8b20f 的上线早于脚本化，当时用的是同样规则的单独块。

## 6. 恢复调度并开放入队

```bash
sudo bash -euo pipefail /srv/quant/upload/m1m2r_<id>/src/deploy/linux/host/release/m1m2_60_resume.sh </dev/null
```

1. 两行镜像名与清单一致，web 健康；
2. 启动 timer；
3. 等到 timer 触发的第一次 runner tick 结束且 `Result=success`（最多 240 秒），并打印这次 tick 的日志；
4. `worker_capabilities` 中有当前数值镜像 ID 的行；
5. 开放入队。

timer 触发的 tick 是调度证据；本节不手工启动 service。

## 7. 私有输入登记（需要单独授权）

```bash
sudo bash -euo pipefail .../m1m2_70_register_inputs.sh /srv/quant/upload/phase5_input/market_research.sqlite [/srv/quant/upload/phase5_ref] </dev/null
```

- hash 必须等于清单中的 `inputs.phase5_bundle.sha256`。
- 结果为 `REGISTERED` 或 `ALREADY_REGISTERED`。字节不同或预期 hash 错误时登记被拒绝，返回非 0，登记库不变。
- 参考账本目录必须正好是 `reference_files` 个文件（330）。
- 上传暂存副本是否删除由所有者决定。

## 8. 发布后验收

1. 从页面提交一次 phase5；
2. 再用相同冻结配置重跑一次；
3. 核对摘要 `1783db80…`、机械复核三层 PASS、330/330；
4. 下载产物与 `X-Content-SHA256` 一致；
5. 结果仍为 0/12；
6. 备份恢复检查为 `OK`；
7. 自然 timer 和主机重启只按真实观察记录。

## 9. 发布中途失败（第 6 节之前）与 10.2 二级回滚

```bash
sudo bash -euo pipefail .../m1m2_102_rollback_full.sh </dev/null
```

**步骤**：

1. 回滚点完整，旧 web 镜像已加载且 ID 一致；
2. 暂停入队；
3. 停 timer；
4. 取研究锁；
5. **取消排队中的 v2 任务**（旧 worker 没有引擎分派，会领取并让 phase5 失败）；
6. `APP_IMAGE` 改回旧镜像并删除 `NUMERIC_IMAGE`；
7. 从回滚点恢复 compose、host 目录、runner、restore-check 和单元文件，删除 job launcher，并逐个比对；
8. 在旧镜像上重建 web 并核对镜像 ID；
9. 删除入队标志；
10. 释放锁并启动 timer。

**数据**：v2 表、产物、参考账本和数据集全部保留，不删除。

**再次发布**：依次执行第 4–5 节和第 6 节。

## 10.1 一级回滚：只停用数值作业

```bash
sudo bash -euo pipefail .../m1m2_101_rollback_numeric.sh </dev/null
```

**步骤**：暂停入队 → 停 timer → 持锁 → 撤销能力（核对没有剩余行）→ 删除 `NUMERIC_IMAGE` → 释放锁并启动 timer → 开放入队。

**结果**：排队中的 phase5 保持 QUEUED，legacy worker 不会领取，页面显示“执行引擎镜像尚未构建登记”。

**恢复**：依次执行第 4–5 节和第 6 节。

## 11. 新闻 collector 升级（单独决定）

不变：news 与 web 共用 `APP_IMAGE`，但只在单独指令下执行 `$C --profile news up -d --no-build --no-deps news`。

## 12. 验证记录

| 项目 | 环境 | 结果 |
|---|---|---|
| 隔离演练（6fe5001 镜像） | 本机测试卷，辅助 Linux 容器 | runner/launcher、取消、超时、SIGTERM、崩溃、恢复、一级和二级回滚均通过（closeout `e6_loop`） |
| 生产发布 | 2026-11-20 08:54–09:31 | 见发布报告；E-1 在此期间发现 |
| **完整脚本 smoke**（本版） | 本机隔离主机：Debian 容器，真实 systemd 作为 PID 1，real `sudo`，本地 Docker daemon，测试项目 `m1m2e` 与测试卷，假值 env 文件 | 对第 3、4–5、6、7、10.1、9/10.2 节的每个脚本完整执行，逐项核对退出码、每步 OK、完成标记，以及实际 env、web 镜像、主机文件 hash、timer 与真实 tick、入队、能力和排队状态。负例见交付包 `e14_ops_scripts/` |

**未覆盖**：生产上的真实回滚（不以真实回滚测试文档修复），以及主机重启。
