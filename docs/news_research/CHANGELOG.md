# Change history / 修改记录

## 1.4 — 2026-11-14（Asia/Shanghai）

- 在独立环境安装官方 CUDA PyTorch 与必需依赖，真实 FP16 矩阵运算和同步通过；记录版本锁与依赖检查。Installed official CUDA PyTorch and required packages in an isolated environment; an actual FP16 matrix multiplication and synchronization passed, with versions and dependency checks recorded.
- 匿名下载官方固定 revision 的 Qwen2.5-3B-Instruct 11 个指定文件，核对本地哈希、两份远端 LFS 哈希、权重索引与 safetensors 可读性，并绑定 `generation_config.json`。Downloaded eleven selected files of the pinned official Qwen2.5-3B-Instruct revision anonymously; verified local hashes, two remote LFS hashes, index and shard readability, and bound `generation_config.json`.
- 固定 v2 冒烟配置与持久化阶段记录；在目标环境重放既有 22 项并新增 3 项 runner 测试，共 25 项不同的合成测试通过。Fixed a v2 smoke configuration and durable phase records; the prior 22 plus three new runner checks passed, 25 distinct synthetic tests total.
- 首次对固定前十篇开发材料完成真实有界推理：十篇全文预检、十次生成开始、九份原始响应回读、一条超时；九份均可解析 JSON，但无一符合冻结 Schema。保存失败与机械检查，不将其称为抽取准确率或金融结果。Ran the first real bounded inference on the fixed ten development articles: ten full-text preflights and generation starts, nine raw responses read back, one timeout; all nine parsed as JSON and none met the frozen Schema. Preserved failures and mechanical checks without an accuracy or financial claim.

## 1.3 — 2026-11-14（Asia/Shanghai）

- 不重新取样，按原始样本、运行脚本与草稿哈希回读；新版画像与旧版 13 组相同语义聚合字段一致。Replayed the existing sample, executed script and review-draft hashes without redownloading; 13 comparable aggregate profile groups matched.
- 修复完整 CSV 的无末尾换行 EOF 边界，保留 Range 截断、坏编码及坏引号的区分；原有 7 项加新增 4 项解析器测试共 11 项通过。Fixed the full-file no-newline EOF edge while preserving prefix/malformed distinctions; seven prior plus four new parser tests passed, 11 total.
- 固定公司事件 JSON Schema、提示词、证据切片校验与独立空白人工裁定材料。Fixed the company-event schema, prompt, span validator and separate blank human-adjudication materials.
- 完成本机模型门槛检查与离线 runner；已检查位置没有可绑定版本的生成模型，实际调用 0 次，推理和金融实验均 NOT_RUN。Completed the local model gate and offline runner; no version-bound generative model was found in checked locations, zero real calls were made, and inference/finance remain NOT_RUN.

## 1.2 — 2026-11-14（Asia/Shanghai）

- 核对原作者 GitHub/Hugging Face 固定版本及 README/LICENSE 商业使用措辞差异；没有推定新闻正文再分发权。Pinned the original-author GitHub/HF releases and recorded the README/LICENSE commercial-use conflict without inferring article-text redistribution rights.
- 匿名分段读取一份有界真实 CSV 样本，原始字段和正文只留本机；发布来源回执与脱敏统计。Read one bounded real CSV sample anonymously; kept raw fields/text local and published a source receipt plus aggregate profile.
- 建立公司—事件标注模板，并对固定顺序前 10 条生成 Codex 字段检查草稿；独立人工金标准、模型推理和金融评估仍未运行。Added a company-event template and a first-10 Codex field-check draft; independent human gold labels, model inference and financial evaluation remain unrun.

## 1.1 — 2026-11-14（Asia/Shanghai）

- 将先前路线整理为逐章对应的中文、英文文档。Published corresponding full Chinese and English roadmaps.
- 新增固定入口、状态文件与来源接入登记，供以后读取当前分支。Added stable navigation, status and source-access records for future retrieval from the current branch.
- 加入用户提出的 Hugging Face 与金十候选来源；核对公开资料，未建立认证连接。Added user-proposed Hugging Face/Jin10 candidates; checked public documentation without authenticated setup.
- 保留研究提案与已执行工作之别；未更改已有 M5 协议或运行金融实验。Preserved proposed/executed distinctions; no existing M5 protocol change or financial run.

来源版本 / Origin: `quant_news_roadmap_20261114.zip`, SHA-256 `a4df9286b315ad6fa91fe5089a99701cb3a1d5308eabcb850821f88d83f4370e`。The prior archive supplies the original Chinese roadmap; this revision adds matching English and source-access updates.
