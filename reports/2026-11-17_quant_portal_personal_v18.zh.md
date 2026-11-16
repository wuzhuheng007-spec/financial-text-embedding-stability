# 个人 Demo v1.8：原文翻译与事件语气分离

执行日期：2026-11-17（Asia/Shanghai）。[配置](../configs/quant_portal_personal_v18_20261117.json)、[冻结输入清单](../results/quant_portal_personal_v18_20261117/selection_manifest_redacted.json)、[代码和输入哈希](../results/quant_portal_personal_v18_20261117/run_freeze_redacted.json)、[逐项计划—实际](../results/quant_portal_personal_v18_20261117/plan_execution.csv)及[聚合回执](../results/quant_portal_personal_v18_20261117/assessment_redacted.json)限定本次结论。私人新闻、原始模型响应和逐条审阅留本机。全部材料均为已接触的开发材料，不是独立测试。

## 计划与实际

| 阶段 | 实际 | 状态 |
| --- | --- | --- |
| P0 冻结 | 五份私人 SQLite 在线备份均可读；保留 v1.7 原八条，按事前顺序另选两条，共十条。混合、指控／否认两个新增类别在固定选择范围内缺失，没有换样本。 | 完成，覆盖有限 |
| P1–P2 三任务与简化契约 | 原文翻译 T_SOURCE 独立于事件提取 A 和条件解释 B；程序分句、编号、保存偏移并回填证据，事件最多处理每篇前三个分段。24 个额外分段保留为预算未运行。 | 代码完成，内容未通过 |
| P3 模型比较 | 固定 Qwen2.5-3B-Instruct revision `aa8e72537993ba99e69dfaafa59ed015b17504d1` 完成 10 次 T_SOURCE、16 次 A；B 因 0 条合格 A 而调用 0 次。唯一候选 7B 的四个权重分片仅完整取得两个，装载与生成均未运行。 | 基线有真实结果；配对比较未完成 |
| P4 翻译 | 10 份原始译文逐条审阅；原始模型译文接受 0 份。1 份经单独、留痕的人名校订后进入所有者私人缓存，在中英页面实看；其余 9 份保持驳回和原文回退。 | 部分完成 |
| P5 前瞻语气 | 私人库保存 16 条候选任务回执，合格前瞻语气 0 条；可用时间没有早于计算完成时间。 | 候选记录已建，合格因子未形成 |
| P6 页面与交接 | 复用登录的本机页面，实看中英约 947px 宽度；修复通用翻译脚本误给保留中文姓名的英文译文添加“待翻译”，修复语言切换回到原新闻附近。当前浏览器不提供 390px 视口控制，窄屏实看未运行。 | 桌面定向通过，窄屏待补 |

## 原始运行与错误

[翻译运行](../results/quant_portal_personal_v18_20261117/run_baseline_t_source_redacted.json)：10/10 尝试、10/10 响应保存、8/10 严格原始 JSON、1/10 机械完整通过、0/10 原始模型译文语义接受。两条 JSON 解析失败；七条触发数字序列机械检查。唯一机械通过的译文把人名改成另一英文人名，因此也被驳回。独立校订只把该错误名称恢复为来源中的原文姓名，旧驳回、原始响应和哈希均保留；这份缓存标为“原文机译经同团队校订”，**不能算模型原始译文通过**。

[事件提取运行](../results/quant_portal_personal_v18_20261117/run_baseline_a_redacted.json)：16/16 尝试、15/16 保存响应、11/16 严格原始 JSON、0/16 全部机械通过，弃答 0。一次模型装载超过冻结时限，子进程终止后才继续下一项。错误含枚举、证据 ID、非候选实体和重复角色；针对性内容检查又发现来源发言人、报道机构和预测性质的混淆。当前“实体必须来自关注别名”的输入契约遗漏了报道中的部分实际主体，这是工程契约问题，不能把所有不通过都归咎于模型。B 的[零调用回执](../results/quant_portal_personal_v18_20261117/run_baseline_b_redacted.json)保持解释自动展示暂停。

唯一候选固定为 [Qwen/Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/tree/a09a35458c702b33eeacc393d103063234e8bc28)，官方模型卡标注 Apache-2.0。计划用 NF4 在本机运行；三次有界续传后仍缺两个分片，[访问回执](../results/quant_portal_personal_v18_20261117/candidate_access_redacted.json)记录最初 HTTP 读取超时及未完成分片。因此没有候选模型权重整体验哈希、显存装载、生成，也没有双模型优劣结论。私人新闻未发送到外部推理服务。

## 页面、研究边界与下一步

独立原文翻译缓存键绑定新闻版本、原文哈希、模型 revision、提示词和目标语言；原文仍可展开。只有经同团队审阅的译文可展示。事件语气候选与合格前瞻记录分层，供应商报道真实性与其文本语气分开。16 条候选记录的 `tone` 均因完整 A 未通过而不进入合格前瞻层；收益、IC、Rank IC、预测、训练、交易均为 `NOT_RUN`。旧 v16.4 自动分析队列继续暂停，行情和金十采集独立运行。

[定向浏览器回执](../results/quant_portal_personal_v18_20261117/browser_targeted_redacted.json)记录登录所有者页面中英切换、来源译文与原文入口、阅读锚点和水平溢出检查；没有把 HTTP 200 代替内容验收。软件检查为 6 个针对性 Python 测试、8 个 Django 权限／个人缓存测试、JS 语法和 Django 系统检查；这些不能证明语义正确或 390px 布局通过。页面刷新只读本机缓存，不调用供应商或模型。旧版已接受卡保留；v2.5 财务输入仍为 `REJECTED_CANDIDATE`。

下一轮单一优先项是修正实体候选范围与逐字段证据绑定，再冻结新开发材料。待 7B 原定 revision 权重完整且哈希核验后，才能在同一协议下运行候选比较；不能用当前负面基线推断候选会改善。ProsusAI/FinBERT 的[提交历史](https://huggingface.co/ProsusAI/finbert/commits/main)显示较晚新增 safetensors；本机核对的旧 `pytorch_model.bin` LFS 哈希、config 与 tokenizer 元数据在 2022 及当前版本相同。模型卡更新时间本身不能证明旧权重包含未来事件；本轮未运行 FinBERT，许可证由[官方模型页](https://huggingface.co/ProsusAI/finbert)标为 CC-BY-NC-SA-4.0。

交付前的[运行复查](../results/quant_portal_personal_v18_20261117/runtime_redacted.json)发现门户可访问，但金十线程曾因超时退出、moomoo 推送曾显示授权过期；在保留缓存的前提下重启独立采集进程后，两条链路及 moomoo 快照均恢复 `CONNECTED`。这是单时点恢复检查，金十此前的分页衔接缺口仍标为可能存在，不据此宣称持续无缺口。
