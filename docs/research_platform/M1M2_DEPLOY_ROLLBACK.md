# M1/M2 部署与回滚步骤（候选，尚未执行）

候选分支 `codex/m1m2-research-platform-20261119`，基线 `3a1fe3e`。生产当前运行 web/news `0030682`、feed `2c7f118`。
以下步骤需要单独的明确指令后才执行；本轮没有改动生产。

## 部署

1. **构建**（从候选提交的 `git archive`）：
   - `docker build -f deploy/linux/Dockerfile --target app --label org.opencontainers.image.revision=<sha> -t quant-personal-app:<short> .`
   - `docker build -f deploy/linux/Dockerfile.numeric --build-arg LOCK_SHA256=$(sha256sum deploy/linux/requirements-numeric.lock | cut -c1-64) --label org.opencontainers.image.revision=<sha> -t quant-research-numeric:<short> .`
2. **传输**：
   - `docker save | gzip` → scp 到 `/srv/quant/upload/`；
   - `sha256sum -c` 校验后执行 `docker load`；
   - 记录本地 image id，未推送 registry，因此没有 registry digest。
3. **先备份**：运行 `quant-backup.sh`。保留 `/srv/quant/backup/host-before-<short>/`，内容包括 private.env、compose.yaml、host 脚本和 backup_scope.json。
4. **更新部署文件**：
   - `deploy/linux/compose.yaml`
   - `deploy/linux/host/{quant-research-runner.sh, quant_research_backup.py, backup_scope.json}`
   - runner 脚本安装到 `/usr/local/sbin/`
5. **在 private.env 中设置**：`APP_IMAGE=quant-personal-app:<short>`、`NUMERIC_IMAGE=quant-research-numeric:<short>`。
6. **启动 web**：`docker compose up -d --no-build --no-deps web`。登记库迁移只做增量（新增列和新表），首次连接时执行，旧记录不改写。
7. **登记默认项**：`docker compose --profile jobs run --rm --no-deps research-runner register-defaults`，登记 v2 规格与 phase5 历史导入，可重复执行。
8. **phase5 输入快照（BLOCKED_INPUTS）**：服务器上没有 phase5 私有 SQLite。需要由所有者把文件放到私有路径，再执行 `worker register-bundle --source <path> --dataset-id phase5_bundle --dataset-version c5b5ec22 --expected-sha256 c5b5ec22b1d3773f6a0345004080d4d2ff10e4cc7d48be21fe13a99cbc6ebe7e`。
9. **作业调度**：quant-research-runner.timer 每分钟在同一 flock 下依次运行 legacy 与 numeric 作业，不另设调度者。数值作业第一次运行时登记 capability，页面随后才允许提交 phase5。
10. **新闻修复（单独决定）**：分页水位、旧删除不覆盖和恢复对账在 news 服务换镜像并重启采集器后生效。本轮未重启采集器。

## 回滚

1. **先处理排队中的 phase5 任务**：旧 worker 不认识 engine 列，会领取并失败。用网页取消，或执行 `UPDATE runs SET status='CANCELLED', cancel_requested=1, exit_reason='CANCELLED_BEFORE_START' WHERE status='QUEUED' AND engine_id IS NOT NULL`。
2. **恢复镜像配置**：private.env 中 `APP_IMAGE` 改回 `quant-personal-app:0030682`，删除 `NUMERIC_IMAGE` 行。
3. **恢复部署文件**：从备份目录恢复 compose.yaml、runner 脚本、quant_research_backup.py 和 backup_scope.json。
4. **重启 web**：`docker compose up -d --no-build --no-deps web`。旧代码会忽略新增表和列，新增数据保留，不删除。
5. **新闻缓存**：新增的 cursors 键（`<provider>:articles:done` 等）对旧代码无害；旧代码继续使用 `updated_since` 键。
