# Quant Portal 个人版端到端验收与历史指引展示

**执行日期：**2026-11-16，Asia/Shanghai。当前协作分支与远端在 P0 均指向 `571eba43df0f8df6c3627a0d04c3fec148832975`；没有重置分支，旧实验和无关未跟踪文件保留。[冻结配置](../configs/quant_portal_personal_acceptance_v2_20261116.json)及[P0–P6 计划—实际台账](../results/quant_portal_personal_acceptance_20261116/plan_execution.csv)记录本轮范围。本轮没有购买云资源、连接行情或新闻，也没有训练模型。

## 个人数据与实际流程

变更前已存在本机账户、6 条自选股、1 条设置及 11 条历史事实卡。[变更前备份回执](../results/quant_portal_personal_acceptance_20261116/ops_prechange_redacted.json)记录了私人 SQLite 在线备份的 SHA-256、完整性和计数，账户及邀请码的值没有进入报告。**本轮没有代用户设置密码，也没有使用个人邀请码。**

独立数据库和端口上的 Microsoft Edge 实际完成邀请注册、登录、退出、重新登录、自选股增删与重复提示、时区持久化、已使用邀请码拒绝、退出后受保护页面跳转，以及两段隔离会话的读取和写入隔离。[浏览器回执](../results/quant_portal_personal_acceptance_20261116/browser_acceptance_redacted.json)列出逐项结果和绑定的代码哈希。第一次自动化过程中的定位器、CSRF 和必填字段用法错误分别保留为测试脚本修正，不算应用通过或应用失败。**自动化测试与用户本人操作分开：本轮未观察到用户本人使用新版页面。**

桌面和 390px 已用真实 Edge 打开注册、登录、主页面、设置和指引区域；窄屏退出按钮换行问题修复后复查，整页未出现横向溢出。[视觉回执](../results/quant_portal_personal_acceptance_20261116/visual_acceptance_redacted.json)记录 22 项通过和 1 项仅合成样例通过，分别覆盖官方事实、无事实空状态及明确标为合成的负数布局。14 条来源链接的地址与打开方式已检查，其中一条实际业绩及一条指引链接在浏览器中打开了对应 SEC 页面；最初一次空白弹窗异常保留，其余 12 条没有逐一导航。私人截图未上传。

## 已有历史指引的展示

导入只使用 SHA-256 固定的[既有 v4 脱敏事实摘要](../results/market_observer_financial_text_v2_20261115/fact_cards_v4_redacted.json)中，训练期最新 NVDA/MU 材料里标为 `CODEX_REVIEW_SOURCE_SUPPORTED` 的记录。[来源回查回执](../results/quant_portal_personal_acceptance_20261116/guidance_source_check.json)将候选 ID、原件哈希和位置绑定到两份官方 SEC 公告。这仍是同一执行团队的开发核对，不是独立人工标准。

页面将 **12 条历史实际事实**和 **2 条未来营收指引**分开。NVIDIA 对 Q1 FY27 披露的中心值为 780 亿美元、容差为 ±2%；按原文公式计算的上下界为 764.4 亿至 795.6 亿美元。[NVIDIA SEC 公告](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000019/q4fy26pr.htm) 美光对 FQ3-26 披露 335 亿美元、容差为 ±7.5 亿美元；计算上下界为 327.5 亿至 342.5 亿美元。[美光 SEC 公告](https://www.sec.gov/Archives/edgar/data/723125/000072312526000004/a2026q2ex991-pressrelease.htm) **上下界是派生值，页面不冒充公司直接披露，也不另造中点。** 新增的美光 FQ2-26 自由现金流属于实际业绩；原件称 adjusted，既有摘要的独立口径字段仍为 `UNKNOWN`，两者在页面分开显示。NVIDIA 当前所选材料没有合格自由现金流记录时，页面提示“尚无已核对数据”，不推断公司未披露。

最终[22 项软件检查](../results/quant_portal_personal_acceptance_20261116/final_software_check_receipt.json)通过，包含事务回滚、输入清单哈希冲突、歧义事实排除、指引范围回算与重复导入。先前 20 项记录发生在导入失败回滚修复之前，和最终检查重叠，不能相加。历史事实卡现为 **14 条**，既有 11 条保留，新增 2 条指引及 1 条已核对的实际现金流。

## 持久化、恢复与剩余限制

[最终运行回执](../results/quant_portal_personal_acceptance_20261116/ops_live_final_redacted.json)显示个人服务已在 `127.0.0.1:8765` 使用最终代码重启，迁移已应用，再次导入新增 0 条；重复启动被拒绝。账户、自选股和设置与备份前的逐行摘要一致。变更前备份在[独立副本](../results/quant_portal_personal_acceptance_20261116/restore_boot_final_redacted.json)完成迁移、启动、临时账户登录及指引显示检查，没有覆盖个人库。[一页中文使用说明](../docs/quant_portal/personal_user_guide.zh.md)给出启动、登录、自选股、指引、交互式密码重置、备份、停止和安全回退步骤；`owner@local.invalid` 只是本机标识，没有邮件找回功能。

[Docker/WSL 诊断](../results/quant_portal_personal_acceptance_20261116/docker_diagnosis_redacted.json)发现 Linux 引擎没有响应，WSL 报虚拟化运行条件不足。由于系统可选功能状态需要管理员权限才能确认，具体缺失组件仍为未知；容器构建、PostgreSQL 联调和云端部署均 **NOT_RUN**。[微软 WSL 故障排查](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting)与[Docker Windows 要求](https://docs.docker.com/desktop/setup/install/windows-install/)是后续检查依据，本轮未改变系统设置或重启电脑。

本轮没有打开四月至七月收益标签、拟合模型或开展正式金融评估。旧版财务文本每事件 **20,000 字符输入预算的证据遗漏**仍未解决；个人页面验收不等于多人容量、新闻有效性或预测能力验收。下一步是用户本人在现有本机账户中查看自选股和历史指引；任何云端多人展示仍需单独完成容器与来源授权门槛。
