# 固定开发材料上的首次真实新闻抽取冒烟

日期：2026-11-14，Asia/Shanghai（UTC+08:00）。本轮只对已有 FNSPID 前十篇开发材料做**回顾性文本工程检查**，不是当年可执行的预测实验。[上一轮 N1 报告](2026-11-14_news_n1_next.zh.md)记录的是模型调用为零的准备状态；本报告对应之后单独绑定的真实运行。

| 阶段计划 | 实际与证据 | 状态 |
| --- | --- | --- |
| A 恢复现场并绑定输入 | 本机原样本哈希与[旧来源回执](../results/news_n1_20261114/source_sample_receipt.json)及 v2 配置一致；未重新下载新闻。三个无关的未跟踪 M5 脚本保留。 | 完成 |
| B 独立环境与 CUDA | 独立 Python 3.11.9 环境安装 `torch 2.11.0+cu128`、`transformers 4.57.1` 等依赖；`pip check` 通过。RTX 5070 Ti Laptop GPU（计算能力 12.0）的 FP16 矩阵乘与同步通过。这是硬件验证，不是模型推理。[环境回执](../results/news_first_inference_20261114/environment_receipt.json) · [版本锁](../configs/news_first_inference_runtime_lock.txt) | 完成 |
| C 官方固定模型 | 匿名获取 Qwen/Qwen2.5-3B-Instruct 固定 revision `aa8e72537993ba99e69dfaafa59ed015b17504d1` 的 11 个指定文件，共 **6,183,463,416 字节**，低于单独的 8 GiB 模型文件预算。本地逐文件大小与 SHA-256 已记录；两份权重与远端 LFS 哈希相符，索引涉及的两个 safetensors 分片可读，`generation_config.json` 已绑定。这里不把所选文件大小写成精确网络传输量。[来源计划](../results/news_first_inference_20261114/model_source_plan.json) · [本机完整性回执](../results/news_first_inference_20261114/model_integrity_receipt.json) | 完成 |
| D 固定配置与验证 | [v2 配置](../configs/news_n1_smoke_v2.json)保留前十篇、旧 Schema/提示词、FP16、eager、不采样、单篇最多 1,024 新 token、最多 10 次调用、单次 180 秒、总 1,800 秒、零重试。runner 增加生成配置绑定及持久化阶段、请求、响应记录。**25 项不同的合成测试通过：解析器 11、契约 6、runner 8；runner 其中 3 项为新增。**[测试日志](../results/news_first_inference_20261114/runtime_test.log) | 工程准备完成 |
| E 真实有界抽取 | 实际 tokenizer 对十篇全文均完成预检，无输入超限；十次 worker 均启动、加载模型并开始生成。九份原始响应保存后回读成功，另一次在固定的 180 秒上限处超时。九份响应均可解析为 JSON 且以 EOS 结束，但全部不符合冻结的 JSON Schema。因无 Schema 合法响应，后续绑定与证据切片的条件分母为零。[独立聚合核查](../results/news_first_inference_20261114/inference_aggregate.json) · [聚合运行回执](../results/news_first_inference_20261114/smoke_receipt.json) | 真实响应已保存；Schema 契约未通过 |
| F 脱敏同步 | 现有协作分支只同步经检查的代码、实际安装版本锁、脱敏来源／环境／完整性／运行聚合回执与双语进度。原始新闻、逐条响应、模型权重、凭据和私人路径留本机；远端提交验证及含画像脚本依赖的小型 GPT 交接 ZIP 见最终交付。 [A–F 逐项计划—实际台账](../results/news_first_inference_20261114/plan_execution.csv) | 完成；远端回执见最终交付 |

本轮已达到“真实响应保存并回读”的工程成功判据。JSON 可解析为**完成响应的 9/9**、全部尝试的 **9/10**；Schema 合法为可解析响应的 **0/9**、全部尝试的 **0/10**。主体绑定及完整机械校验的条件分母均为零，条件通过率应记为 **NOT_EVALUATED**；在全部尝试分母上均为 0/10。一条调用超时，九份保存的响应均以 EOS 结束。[聚合核查回执](../results/news_first_inference_20261114/inference_aggregate.json)将计数绑定到输入、模型、配置、提示词、Schema 和 runner 哈希。失败是真实结果，本轮未在看到输出后改写提示词或 Schema。 原始新闻、完整提示词、实际生成文本与 token ID、逐条解析结果和失败记录仅保存在本机忽略目录。公开回执只给计数与分母。JSON 可解析、Schema 合法或 Unicode 原文证据位置吻合都只是**机械检查**，不能证明事件、目标公司或影响方向判断正确。

前十篇此前已经用于字段设计，属于单一候选 ticker 的有界文件前缀开发材料，不是独立测试集。人工审阅栏仍为空，因此不能计算抽取准确率、F1、人工一致性或校准概率。新闻首次可用时间、历史公司—证券身份与模型确切训练截止仍未证实；将该模型用于 2023 年新闻属于回顾性工程工作，不能据此声称无前视预测。

**下一步：**在不改写本次固定提示词与配置的前提下，审查本机保存的响应和失败原因；需要调整时建立新版本。独立人工裁定后，再用另一个样本并按事件簇隔离评价抽取质量。金融方向仍需历史新闻可用时间、主体身份、价格与日历规则及被接受的完整协议。金融标签、预测、收益与交易评估均 **NOT_RUN**。

本轮核对了[官方固定模型文件页](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/tree/aa8e72537993ba99e69dfaafa59ed015b17504d1)和[固定版许可](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/aa8e72537993ba99e69dfaafa59ed015b17504d1/LICENSE)。用途限定非商业研究；不从模型许可推断新闻正文再分发或商业部署权利。
