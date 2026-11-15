# 财务证据输入 v2.2：固定预算下的负面结果

**执行日期：2026 年 11 月 16 日，Asia/Shanghai。** 本轮只复用 2025-04-01 至 2026-03-31 已保存的 NVIDIA 与 Micron 训练期 SEC 材料：八份财报附件、八份同 accession 主申报，组成八个事件。模型输入每事件固定 **20,000 个 Unicode 字符**。[最终预检](../results/market_observer_financial_text_v22_20261116/preflight_r3_redacted.json)绑定来源清单、代码、配置和参考文件哈希；[v2.2 配置](../configs/market_observer_financial_text_v22_20261116.json)、[旧版报告](2026-11-15_market_observer_financial_text_v2.zh.md)及[v2.1 结果](../results/market_observer_financial_text_v21_20261115/retention_redacted.json)均保留原样。

## 实际构建与核对

v2.2 将编号的公共上下文及候选证据组序列化进模型实际可见文本。唯一一次真实构建在八个事件中选入 **147 组**，每事件实际文本 **9,068–17,879 字符**，均未超预算。另有 **231 组**被省略：**109 组**缺必要上下文，**122 组**上下文关系未解决，**0 组**因预算省略。已选入片段中 **338 个**按保存的原始文档 DOM 位置回查；这仅验证定位，不等于财务事实正确。[构建回执](../results/market_observer_financial_text_v22_20261116/build_redacted.json)记录逐事件数量及输入哈希。原文与完整模型输入留在本机。

冻结清单有 **71 条候选事实**。同一执行者另行核对来源后，**60 条有来源支持**、**11 条仍有歧义**；歧义项没有升级为确认事实。[旧损失阶段回执](../results/market_observer_financial_text_v22_20261116/loss_stage_redacted.json)中的“25 条因预算省略”描述的是 **v2.1 旧选择器**，并不与本轮 v2.2 的零预算省略相矛盾。[统一保留比较](../results/market_observer_financial_text_v22_20261116/retention_redacted.json)给出：

| 固定分母 | 旧版输入 | v2.1 实际输入 | v2.2 实际输入 |
| --- | ---: | ---: | ---: |
| 71 条候选中，片段级严格完整 | 34 | 18 | 8 |
| 其中 60 条来源支持候选中，片段级严格完整 | 34 | 18 | 8 |

第二行是限定分母，不能与第一行相加。按冻结的片段级检查，v2.2 **退步**；旧版完整而 v2.2 不完整的来源支持记录有 **26 条**，包括已保存的 2025 年 6 月 Micron 营收、EPS 和毛利率行。另行检查明确证据组关系后，v2.2 **没有一条**来源支持事实的全部必要信息被绑定在单个模型可见组中：**46 条**无完整单组，**10 条**脚注关系未解决，**4 条**可见字段存在但来源会计口径未知。因此上述 **8 条**“片段级完整”不能写成八条已核实、可直接交给模型的完整事实。v2.2 不晋升到个人财报卡或预测路径。

### 逐候选失败诊断

比较完成后进行的**只读**[诊断](../results/market_observer_financial_text_v22_20261116/postrun_loss_diagnosis_redacted.json)，把 71 条候选的数值位置追到已保存的来源块：**27 条**对应选入的数值组，**40 条**对应省略的数值组（其中 **34 条**上下文关系未解、**6 条**缺必要上下文），另 **4 条**所在块没有形成证据组。在 60 条来源支持候选中，**24 条**对应选入组、**36 条**对应省略组。这里数的是**候选事实**，不能与前文 147 组选入、231 组省略的**证据组**数量混用；没有候选组因预算被省略。八条片段级“完整”记录的期间均出现在同一文档的别处，但未绑定到被选入的数值组。DOM/文本匹配不等于重新确认来源；只有文本匹配、多重匹配或表格中其他数值列仍可能产生歧义。[诊断修正记录](../results/market_observer_financial_text_v22_20261116/postrun_diagnosis_corrections.json)保留两次脚本错误，并注明冻结的适配器及比较没有重跑。

## 计划—实际逐项对照

| 阶段 | 计划 | 实际与证据 | 状态及后续 |
| --- | --- | --- | --- |
| P0 | 冻结训练范围、比较规则、输入与哈希 | [预检](../results/market_observer_financial_text_v22_20261116/preflight_r3_redacted.json)记录 16 份原件、71 条候选、预算与实际哈希。 | 已完成；保留版本。 |
| P1 | 区分解析、关系与预算损失 | [旧损失阶段](../results/market_observer_financial_text_v22_20261116/loss_stage_redacted.json)按 v2.1 状态归类；[运行后诊断](../results/market_observer_financial_text_v22_20261116/postrun_loss_diagnosis_redacted.json)追踪候选数值位置，未重跑比较。 | 描述性诊断完成；歧义项仍有歧义。 |
| P2 | 建立模型可见的公共上下文与完整证据组 | [构建回执](../results/market_observer_financial_text_v22_20261116/build_redacted.json)记录一次真实构建、147 组选入、231 组省略和 DOM 回查。 | 实现完成；内容准入未通过。 |
| P3 | 在看到保留分数前冻结通用规则 | [预检](../results/market_observer_financial_text_v22_20261116/preflight_r3_redacted.json)和[修正记录](../results/market_observer_financial_text_v22_20261116/corrections_redacted.json)分开记录合成案例/运行前修复与比较器修复。 | 已完成；未按分数改适配规则。 |
| P4 | 用同一清单比较旧版、v2.1、v2.2 | 首次比较尝试因事件日期字段缺失，在输出前失败；从冻结来源块取日期并核一致性后，完成一次比较。[结果](../results/market_observer_financial_text_v22_20261116/retention_redacted.json)为 34 / 18 / 8。 | 负面结果已完成；v2.2 不晋升。 |
| P5 | 合成边界与真实来源定位核对 | [最终日志](../results/market_observer_financial_text_v22_20261116/synthetic_tests_final_r3.log)有 **23 项合成检查通过**；此前 20、22 项是重叠修订版，不累加。真实构建和 DOM 检查单列。 | 范围内完成；不证明来源事实正确。 |
| P6 | 保持个人服务；单列容器阻塞 | [页面进度](../results/market_observer_financial_text_v22_20261116/site_progress_redacted.json)显示个人应用与页面均未修改；[部署阻塞说明](../docs/quant_portal/deployment_blocker_v22_20261116.zh.md)复用先前 Docker/WSL 诊断。 | 个人版不变；容器和云端 `NOT_RUN`。 |
| P7 | 中英报告、状态、选择性 Git 同步及脱敏交接 | 文档已准备；[计划—实际台账](../results/market_observer_financial_text_v22_20261116/plan_execution.csv)为机器回执。撰写时 Git 推送、远端回读及 ZIP 核验仍为 **PENDING**。 | 核实完成前不写同步成功。 |

## 限制和下一步

本轮是训练材料的内容工程检查。规则设计者此前已见过四至五月验证结果，不能将其称为独立抽取评价。原件判断属于同一执行者 `CODEX_REVIEW`，DOM 匹配只能说明位置可回查。本轮**未读取四至七月原文或标签，未新增下载，模型拟合与推理均为 0，也未做新收益标签、回测或交易**。正式 M5 准入仍为 `NOT_RUN`。个人版、页面和 dot 连接状态均未改变。

下一轮只优先处理一项：**让指标、数值、期间、单位和会计口径在同一个模型可见证据组中正确绑定，同时避免丢掉原有数值行**。先用冻结的 60 条来源支持训练期候选逐条定位失败，再修通用关系规则；保留本轮负面结果作为对照。不要用已暴露的验证期或受污染测试期选择修复方案。
