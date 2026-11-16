# Quant Portal 个人测试版使用说明

本版只在这台电脑的 `127.0.0.1:8765` 运行。自选股是待核实的证券代码；财报卡是有来源定位的**历史训练期材料**，不是最新行情或投资建议。页面现在读取单独后台进程保存的私人 moomoo 行情和金十快讯；其他账户不能读取这些私人缓存。

## 启动与登录

在项目根目录打开 PowerShell，运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/start_personal.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/status_personal.ps1
```

打开 <http://127.0.0.1:8765/>。状态显示 `RUNNING` 时不要重复启动。首次使用请在[注册页](http://127.0.0.1:8765/accounts/register/)填写本机账户标识 `owner@local.invalid`，从未纳入 Git 的 `apps/quant_portal/var/personal_invite.txt` 私人文件读取一次性邀请码，并自行设置密码。已有账户直接登录；不要重复注册或分享邀请码。这个地址仅是本机登录标识，**没有邮件找回密码功能**。

忘记密码时，在自己的 PowerShell 中运行以下命令，按交互提示输入新密码；不要把密码写在命令参数、聊天或截图里：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/reset_personal_password.ps1
```

## 私人行情和快讯

首页每 5 秒读取一次本机缓存，不会因此请求供应商。后台初始订阅六只已核对代码 TQQQ、QQQ、SOXX、SPY、NVDA、MU，并每 60 秒最多读取一页最新金十快讯。自选股中的“订阅行情”与“停止订阅”由后台执行；页面先显示等待确认，收到服务端确认后才显示已订阅。六个订阅键是本机预算，不是已核实的账户总配额；满额时必须在页面明确选择要替换的一只，系统不会自动踢出其他证券。先停止订阅并等待确认，才能从自选股移除该证券。新代码只有已有官方映射核实后才能订阅。

可切换“全部快讯”“我的关注”和“重点事件”。重点事件可按公司或 ETF、类别、时间范围筛选；“关注原因”显示匹配短语，排序是规则排序，不是市场影响预测。展开“编辑科技股关注预设”可以修改对象和类别；“填入科技股预设”只改变表单，点击“保存关注范围”才生效。默认沿用原有自选、独立新闻关注和宏观主题，不会自动全选。另有“闪迪”“SK 海力士”“SpaceX”独立新闻关注开关，与行情订阅分离；没有核实可用代码的对象不会出现虚构报价。阅读旧消息期间有新消息时，页面显示提示，点击后再回到最新。快讯标为“金十快讯／原始发布者未知／待交叉核实”，有来源入口时可打开；缺入口会明确显示。疑似相关报道可展开逐条查看，不代表独立确认。

行情分别显示来源时间、本机收到时间、采集心跳、最近推送和最近新增报价。休市时显示最后可得报价及时间，不表示此刻有成交；连接活跃与报价更新是两个状态。延迟、币种或账户配额若未核实会显示未知。新闻时间如无时区依据也会明确提示。未订阅证券的历史缓存会标记“未订阅”；没有报价的证券显示原因。行情和快讯均不构成交易建议。

若要在美股实际常规交易时段手动核对来源时间是否推进，可在项目根目录执行：

```powershell
python -m scripts.quant_personal_feed.open_market_acceptance --config configs/quant_portal_personal_feeds_v11_20261116.json --seconds 300
```

回执只存放在被忽略的本机目录。休市检查不能代替开市验收；程序也不会因价格不变而虚构新成交。

若两条数据都未更新，先运行下方 `status_personal.ps1`；它分别显示门户和采集进程状态。需要单独重启采集时运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/feed_personal.ps1 stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/feed_personal.ps1 start
```

## 自选股与财报卡

主页面的“加入自选”输入证券代码、可选名称和类别后提交。页面会提示重复代码；删除前请确认目标行。修改后刷新页面检查保留状态。“个人设置”中可切换显示时区。代码关联只是观察候选，不等于已核实历史证券身份。

“财报事实卡”区域分别展示实际业绩与未来指引。查看指标、适用财期、原始数值或范围、单位、会计口径、来源链接与检查状态；未知字段保持 `UNKNOWN`。指引上下界由原文中心值与容差按页面公式计算，并非公司直接披露的上下界；本页不生成中点。自由现金流等主题若没有合格记录，页面应显示“尚无已核对数据”。历史开发事实不得理解为当前季度、实时预测或已验证的模型输出。

## 备份、停止与常见问题

个人数据库和账户只保存在本机被 Git 忽略的应用目录。运行下列命令做在线备份；回执及数据库副本也保持私人。恢复演练只在独立副本中检查，不覆盖正在使用的库。

```powershell
python apps/quant_portal/ops/backup_personal.py
python apps/quant_portal/ops/restore_probe_personal.py apps/quant_portal/backups/<备份文件名>.sqlite3
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/stop_personal.ps1
```

若页面打不开，先运行 `status_personal.ps1`；显示 `STOPPED_OR_UNTRACKED` 时再启动。若端口被其他进程占用，启动脚本会拒绝接管，应先查明占用者。若财报卡缺失，检查导入回执及来源哈希，不要补写数字。行情或新闻显示断开时，旧缓存仍会保留并标明状态；不要据此判断今天没有新闻或成交。

确需回退个人数据库时，先停止服务，再运行 `backup_personal.py` 为**当前**库另留一份新备份。选定要恢复的旧备份，运行 `restore_probe_personal.py` 校验其私人回执、SHA-256 与完整性；核对文件名和目录后，确认 8765 端口没有监听进程，再执行下面的单文件复制。不要删除备份或在服务运行时覆盖数据库。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/stop_personal.ps1
python apps/quant_portal/ops/backup_personal.py # 为当前库另存新备份
$backupFile = 'apps/quant_portal/backups/personal-YYYYMMDDTHHMMSSZ.sqlite3' # 改成已校验的准确文件名
python apps/quant_portal/ops/restore_probe_personal.py $backupFile
$backupDir = (Resolve-Path -LiteralPath 'apps/quant_portal/backups').Path
$source = (Resolve-Path -LiteralPath $backupFile).Path
if (-not $source.StartsWith($backupDir + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw '备份路径超出私人目录' }
if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { throw '先停止服务' }
$target = Join-Path (Resolve-Path .).Path 'apps/quant_portal/var/dev.sqlite3'
Copy-Item -LiteralPath $source -Destination $target -Force
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/start_personal.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File apps/quant_portal/ops/status_personal.ps1
```

当前启动脚本会执行迁移与来源核对导入，因此**复制旧库后再启动并不等于回退应用代码**；若要同时回退代码，需先在隔离副本上验证对应版本。本轮只做了隔离恢复演练，没有执行上述真实回退。
