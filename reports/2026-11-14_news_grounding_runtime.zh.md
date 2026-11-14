# N1 证据定位抽取：逐字引用与有界 GPU 运行

日期：2026-11-14，Asia/Shanghai（UTC+08:00）。这是在**同一批前十篇开发材料**上的第三个工程版本，不是独立抽取质量评价或金融预测实验。[v3 契约修复报告](2026-11-14_news_schema_repair.zh.md)及其[审计聚合](../results/news_schema_repair_20261114/new_inference_aggregate.json)保持不变。

## 计划与实际

| 阶段 | 计划 | 实际与证据 | 状态 |
| --- | --- | --- | --- |
| A · 诊断保存的失败 | 只读检查旧原文、响应、理由矛盾和 OOM；区分引用唯一、重复和不存在。 | [旧结果诊断](../results/news_grounding_runtime_20261114/old_failure_diagnosis.json)显示，旧 **3** 条证据引用中 **2** 条在原文中唯一逐字存在、**1** 条存在多处、**0** 条不存在；模型原报位置 **0/3** 匹配。理由矛盾已核对，具体逐条值仅留本机。第 3 次旧调用进入生成后遇 CUDA 内存不足；旧运行没有逐阶段显存数据，具体资源压力及提示词长度的因果作用均为 **UNKNOWN**。 | 完成 · 离线基线 |
| B · 接入独立版本的定位契约 | 保留 v3；另建 wire v2、短提示词、严格引用适配器、v4 配置及拒绝不匹配输入的校验。 | [wire Schema](../docs/news_research/company_event_wire_v2.schema.json)、[v3 提示词](../docs/news_research/company_event_prompt_v3_quotes.md)、[适配器](../scripts/exact_quote_adapter.py)和[v4 配置](../configs/news_n1_smoke_v4_grounded.json)已另版安装。[候选回读](../results/news_grounding_runtime_20261114/candidate_validation_replay.json)中的 **15 项合成断言**不是模型输出。版本化 runner、auditor 和 comparator 通过[运行前合成测试](../results/news_grounding_runtime_20261114/pre_run_validation.log)：**52 项 pytest 测试**，其中含已跑过的 33 项；没有模型调用。15 项断言与 52 项测试是不同的验证调用，不能相加当成独立研究试验。 | 完成 · 运行前工程 |
| C · 记录真实运行条件 | 检查当下 GPU 与进程，预检十篇全文输入，在固定 worker 阶段记录显存。配置 `sdpa` 不等于证实使用某个具体 kernel。 | [运行前门槛](../results/news_grounding_runtime_20261114/prerun_gate_receipt.json)显示十篇全文输入 **10/10** 可容纳于 32,768 token 上限并预留 1,024 个输出 token（提示词 2,160–3,502 token）；该时刻可用显存 7,364 MiB，另有七个活跃计算进程，没有旧新闻 worker。[运行后审计](../results/news_grounding_runtime_20261114/inference_aggregate.json)回读 **46/46** 份可用的 worker 阶段 CUDA 快照；观测最小空闲为 **0 字节**，各 worker 峰值已分配显存的最大值为 **8,447,246,336 字节**。实际 SDPA kernel 为 **UNKNOWN**。这些值不能单独解释另一次进程中断。 | 完成 · 条件已测量 |
| D · 一次有界 v4 开发运行 | 固定 Qwen 版本、FP16、seed 1729、相同前十篇和顺序；最多十次调用、每次最多 1,024 个新 token、180 秒、零重试；OOM 即保存并停止。 | 同一次 v4 运行在本地时间 16:16:58–16:39:43 尝试了固定顺序的 **10/10** 篇，保存 **9** 份原始响应。第 2 次发起后，原父进程未保存该次响应即中断；Windows 同期记录 `python.exe` 与 `codex.exe` 崩溃，因果关系 **UNKNOWN**。[中断观察](../results/news_grounding_runtime_20261114/native_interruption_observation.json)及[审计恢复记录](../results/news_grounding_runtime_20261114/inference_aggregate.json)将该次记为 `INTERRUPTED_PROCESS_UNCLASSIFIED`，并未冒称 OOM。[原 runner 源码](../results/news_grounding_runtime_20261114/original_v4_runner.py)与恢复 runner 哈希分列；恢复未重试前两篇、继续遵守原始 1,800 秒总期限，模型输入契约没有改变。 | 有界运行完成 · 一次中断 |
| E · 审计并脱敏同步 | 分别报告原始 wire、逐字引用与主机构造 canonical 的指标及分母；原文和回答留本机；双语进度推送既有 GitHub 分支并回读。 | 独立的 [v4 审计](../results/news_grounding_runtime_20261114/inference_aggregate.json)通过：已保存响应中 JSON 可解析 **8/9**、wire 合法 **1/8**；共核查 8 条引用，**7/8** 唯一逐字匹配、**1/8** 未找到；wire 合法响应中 canonical 可构造 **0/1**。[配对状态审计](../results/news_grounding_runtime_20261114/paired_comparison.json)覆盖固定十个位置并通过，但仅**一个**位置新旧均有完整响应。双语文档与脱敏结果纳入本次分支更新，远端推送与回读另在交接记录中核验。 | 完成 · 审计与文档；远端验证见交接 |

[A–E 逐项计划—实际台账](../results/news_grounding_runtime_20261114/plan_execution.csv)对应任务中的各项要求及未完成分支。[v4 配置](../configs/news_n1_smoke_v4_grounded.json)固定原 FNSPID 样本哈希、模型版本、wire 与 canonical Schema、提示词、适配器、原校验器、注意力实现请求和预算。v3 配置及旧运行保留原状。

## 新流程各阶段的含义

模型输出的是带事件语义字段与正文引用的**原始 wire JSON**，不输出 `input_binding` 或字符位置。主机先校验 wire Schema 和正文哈希，再对每条引用做区分大小写、不规范化的 Unicode 逐字检索。引用不存在或重复时，不任取一处、不改写事件或方向，也不构造 canonical 对象。全部引用唯一时，主机附加可靠输入绑定和计算得到的位置，再调用未改动的 canonical v1 校验器。合法的零引用输出单列为 `NO_SPANS_REQUESTED`，不能算作引用通过率 100%。

十个尝试位置的终止状态分布为 **7 个 `WIRE_SCHEMA_INVALID`**、**1 个 `JSON_PARSE_ERROR`**、**1 个 `QUOTE_UNRESOLVED`** 和 **1 个 `INTERRUPTED_PROCESS_UNCLASSIFIED`**。九份原始响应中八份 JSON 可解析。唯一一份 wire 合法响应请求了八条引用，其中七条在原正文中恰有一处匹配，一条不存在；适配器因此拒绝构造 canonical，对 wire 合法响应的可构造率为 **0/1**。由于构造数为零，canonical Schema 与最终机械通过的条件分母为零，均记 **NOT_EVALUATED（0/0 已构造）**。wire 合法响应中的 `NO_SPANS_REQUESTED` 与空 records 均为 **0/1**。[各阶段数量和分母](../results/news_grounding_runtime_20261114/inference_aggregate.json)描述工程运行，不能作为公司事件语义正确率。

此前 v3 轮尝试 3/10 篇、保存两份响应；v4 尝试 10/10 篇、保存九份，但有一次外部进程中断。[配对回执](../results/news_grounding_runtime_20261114/paired_comparison.json)仅有一个位置新旧均有完整响应。固定十篇的新提示词 token 中位数比 v3 **少 1,515.5**。提示词长度、wire 契约、主机适配器和注意力实现请求同时改变。v4 未记录 PyTorch OOM，但同期原生进程崩溃且共享 GPU 占用变化，不能将差异归因为 SDPA。运行前原 runner 与恢复 runner 为不同代码哈希；[恢复验证](../results/news_grounding_runtime_20261114/recovery_validation.log)有八项合成测试，[恢复后验证](../results/news_grounding_runtime_20261114/postlaunch_recovery_validation.log)另有一项聚焦合成测试，[运行后验证](../results/news_grounding_runtime_20261114/post_run_validation.log)有 20 项聚焦合成测试。[运行前测试](../results/news_grounding_runtime_20261114/pre_run_validation.log)共 52 项，含旧测试；这些测试调用存在重叠，不是独立抽取实验。[最终审计闭环](../results/news_grounding_runtime_20261114/final_audit_closure_receipt.json)记录 v3、v4 及配对回执的只读重放逐字节一致。

v3 的**模型自报位置 0/3 匹配**与 v4 的主机生成位置核对不是同一指标。主机附加绑定也不是模型复制哈希的能力。字符串存在不证明它支持所述公司、事件或影响方向。新提示词、wire 协议、适配器和 SDPA 请求同时改变，本轮开发运行无法识别单一环节的效果；仅配置 `sdpa` 也不能证明实际 kernel 或 OOM 已解决，详见对应版本的 [Transformers 注意力接口](https://huggingface.co/docs/transformers/v4.57.1/attention_interface)与 [PyTorch SDPA 文档](https://docs.pytorch.org/docs/2.11/generated/torch.nn.functional.scaled_dot_product_attention.html)。

## 研究边界与下一步

这前十篇此前已用于字段与提示词开发。抽取质量结论仍需独立人工公司—事件裁定及隔离事件簇的新样本。历史发布时间、公司—证券身份和已接受的金融协议是另行解决的前提。正式金融标签、拟合、预测与收益评价均 **NOT_RUN**。

保留原始响应及各阶段记录，在本机检查七份 wire 格式失败与一条引用缺失；继续核查原生进程中断和所测显存条件，不推定单一原因。若更改提示词或运行配置，应另立明确版本并用独立样本评价；本轮只完成一次有界 v4 运行。人工抽取质量、历史金融准入和收益实验均尚未启动。
