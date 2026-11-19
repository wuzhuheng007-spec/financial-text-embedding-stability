# 第二阶段复现说明

分析固定输入身份；不要覆盖仓库已有结果。原始作者文件不在此再分发。下载辅助程序根据固定Git提交及SHA-256取回当前和历史作者文件，并检查NYU工作簿身份；若NYU今后换版或网络拒绝，会明确失败，不静默换数据。该辅助下载程序本轮未执行，本轮真实输入由GitHub读取并核对Git blob身份。

以下从本目录在PowerShell执行，使用一个尚不存在的新目录：

```powershell
New-Item -ItemType Directory -Path ..\phase2_rebuild
Copy-Item .\*.py, .\PLAN_zh.md, .\freeze_receipt.json, .\source_registry_complete.json, .\revision_history.json ..\phase2_rebuild\
cd ..\phase2_rebuild
py -3 .\fetch_author_sources.py --out .\source_audit
py -3 .\diagnose_bw.py --workbook .\source_audit\SENTIMENT_nyu.xlsx --author-workbook .\source_audit\SENTIMENT_github.xlsx --out .\results\bw_diagnostic
py -3 .\self_check_bw.py --workbook .\source_audit\SENTIMENT_nyu.xlsx --results .\results\bw_diagnostic
py -3 .\audit_revisions.py
```

依赖numpy、pandas、openpyxl。实际本轮版本见results/bw_diagnostic/run_manifest.json。若已保留正确输入，可放入source_audit，下载程序核对哈希后复用。NYU原文件必须为3580398aaba6b72ad988844416c7b0ac9caca24fd2ab1d9149a983ec63b9f7a2，不同字节版本不能冒充原输入。

如需重新执行新闻字段抓取与报告生成，再安装matplotlib并运行：

```powershell
py -3 .\news_field_pilot.py
py -3 .\build_report.py
```

新闻抓取会读取当前网页并记录新时间及哈希，不能声称重现当年的首次可用文本，也不保证与本轮快照逐字节相同。报告脚本保留历史实验日期和固定范围；新的复现时间由各自运行回执记录。没有运行Stata或收益模型；SVD检查由同一执行方完成，不是外部独立验证。GitHub提交日期不能替代首次公开发布时间。
