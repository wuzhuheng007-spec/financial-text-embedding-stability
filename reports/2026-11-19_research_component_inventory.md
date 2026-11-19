# 研究组件盘点：数据集、因子、回测、训练（2026-11-19）

- **基线**：仓库 `b2f2a32`（PR #4 分支，包含 PR #5 和并行研究提交 `89ce71a`、`0e6afda`）。
- **方法**：只读核查代码、配置、测试和文档。没有运行训练，也没有请求任何数据源。
- **状态定义**：
  - **已有且可运行**：有入口，并有使用合成数据或测试夹具（fixture）的测试覆盖。真实运行一般还需要私有数据或授权。
  - **部分实现**：只有代码或设计，不完整，或依赖拿不到的数据。
  - **缺失**：检索后没有找到。
- **复核**：Claude 抽查了以下结论，均成立：
  - 因子注册表缺失：检索 `factor_registry`、`factor_id` 无结果。
  - 交易成本没有实现：检索结果只有行情原始字段 `turnover_raw`。
  - 微调缺失：检索 LoRA、PEFT、Trainer，只命中单词 "exploratory" 中的字母，属误报。
  - 可复用的组件确实存在：`fold_specs` 在 `scripts/news_sentiment_factor_v3/evaluate.py:70`，另有 `research/market_sentiment/record_step.py` 和 `src/m5_bridge/evaluate.py`。
- **数据位置**：大多数研究脚本依赖被 gitignore 的私有数据 `artifacts/`，本 worktree 没有这个目录。主仓库的 `artifacts/` 里没看到行情库或 WRDS 导出；其他位置是否有，未核实。

## 1. 数据集

| 组件 | 状态 | 关键文件 | 入口/命令 | 依据 | 阻塞 |
|---|---|---|---|---|---|
| 个人 feed 报价/新闻缓存（sqlite） | 已有且可运行 | `scripts/quant_personal_feed/store.py:98-180`；`collector.py`；`cli.py` | `python -m scripts.quant_personal_feed.cli`；compose 服务 `feed` | `tests/test_quant_personal_feed.py`、`test_store_concurrent_init.py` 等 | 需要 moomoo/金十授权；只有实时缓存，没有历史回填 |
| 日线行情（moomoo 研究库） | 部分实现 | `research/market_sentiment/data_audit/code/build_dataset.py`、`verify_dataset.py`、`series_registry.json` | `build_dataset.py`（离线合并） | 有登记表和合并回执，没有 pytest | 原始归档是私有的，不在仓库中 |
| 日线行情（其他来源，**重复实现**） | 部分实现 | `src/finembed/m4.py`（Massive）；`scripts/market_observer_historical_v1/price_baseline.py`；`news_sentiment_factor_v3/price.py`；`v44/price_pilot.py`；`v45/daily_prices.py` | `finembed m4`；v45 worker；compose 服务 `daily-price` | `tests/test_m4.py`、`test_market_observer_historical_v1.py`、`v45/test_v45.py` | 需要 Massive 密钥或 moomoo；v45 合格标签数为 0 |
| 分钟线/盘中 | 部分实现 | `scripts/market_observer_v1/store.py:85`、`moomoo_ws.py`、`evaluation.py`（30 分钟） | `market_observer_v1/cli.py` | `tests/test_market_observer_v1_eval.py` | 没有历史分钟数据 |
| 金十新闻语料 | 已有且可运行 | `scripts/jin10_mcp/{store,engine,cli,export}.py` | `python -m scripts.jin10_mcp.cli start/status` | `tests/test_jin10_ingest.py`、`test_jin10_mcp_http.py` | 需要金十账号；有权利限制 |
| SEC EDGAR 导入器（**两套以上，重复实现**） | 已有且可运行 | 研究用 10-K：`src/finembed/sec.py`；门户元数据：`scripts/quant_cloud_official/sec_filings.py` | `finembed ingest`；`sec_filings.py`；systemd 定时器 | `tests/test_sec.py`、`test_cloud_official_sec.py` | 只存元数据和原文链接 |
| 10-Q / 8-K 财报正文 | 部分实现 | `scripts/m7_*`、`m8_*`、`m9_*`、`src/finembed/m8_tenq.py` | 一次性脚本 | 有测试，但只有 5 家公司 | 正文在私有 artifacts 中 |
| 电话会/逐字稿 | 缺失 | — | — | 没找到导入器 | — |
| FNSPID 新闻样本 | 部分实现 | `scripts/sample_fnspid_news.py` | 脚本 | `tests/test_fnspid_bounded_sample.py` | 只是有上限的工程样本 |
| WRDS/CRSP/CCM 链接 | 部分实现 | `scripts/audit_m5_*`、`prepare_m5_daily_rows.py` | 审计脚本 | `test_audit_m5_*`；M5 状态为 `PROPOSED/NOT_RUN` | 需要 WRDS 授权导出 |
| Compustat | 缺失 | — | — | 只在文档中被提到 | — |
| 宏观数据 | 部分实现 | `scripts/build_macro_tech_catalog.py`、`macro_tech_expectation.py` | 脚本 | `test_macro_tech_*`；只在合成数据上运行 | 缺一致预期数据源 |
| 财报/业绩数据 | 部分实现 | `assemble_earnings_intake.py`、`derive_earnings_growth.py` | 脚本 | `test_earnings_*` | 只有 6 家公司，人工采集 |
| 快照/版本（manifest、哈希） | 部分实现（分散） | `MANIFEST_SHA256.json`；`v42/snapshot.py`；`v43/daily.py:82-113`；`market_observer_v1/learning.py:184` | 各版本各自的冻结/校验脚本 | 每个版本都有哈希校验 | 缺统一的数据集 ID 和版本层 |
| 可用时间字段 | 已有（规范较完整） | feed 表的 `provider_time/source_at/received_at/published_at_utc`；`historical_alignment.py` 的 `first_available_at` | — | `test_market_observer_historical_alignment.py` | 历史数据的首次公开时间未解决 |
| 交易日历（**两套，重复实现**） | 已有且可运行 | `scripts/build_nyse_calendar_round9.py`；`configs/news_sentiment_factor_v42_us_calendar_2026.json` | builder 脚本 | `tests/test_build_nyse_calendar_round9.py` | — |

## 2. 因子/信号

| 组件 | 状态 | 关键文件 | 入口/命令 | 依据 | 阻塞 |
|---|---|---|---|---|---|
| 英文标题语气（LM/FinBERT） | 部分实现 | `scripts/news_sentiment_factor_v4/score.py` | v4 模块 | `test_news_sentiment_factor_v4.py`；门户已在读取 | 私有缓存 |
| 中文语气 v4.1–v4.5 | 部分实现 | `v41`…`v45`（worker、score、association、snapshot、labels） | `python -m scripts.news_sentiment_factor_v4x.worker` | 有测试；v45 门控：0 个快照批次，0 个因子-收益对 | 样本还在积累，没有 IC |
| 财报事件语气 F1/F2（v2/v3） | 部分实现 | `v2/factors.py`、`v3/factors.py` | 一次性脚本 | `test_news_sentiment_factor_v2/v3.py` | 私有评分和价格 |
| 嵌入表示（MiniLM ONNX） | 已有且可运行 | `src/finembed/model.py`、`analysis.py` | `finembed model-fetch/analyze` | 测试较多；`verify-output` 可复算 | 只覆盖 5 家公司的 10-K Item 1 |
| 事件特征/抽取规则 | 部分实现 | `quant_personal_feed/event_rules.py`、`configs/news_company_event_contract_v1.json` | — | `test_quant_personal_events_v12.py` | — |
| 因子定义注册表/因子版本 | **缺失** | 只按目录版本化（v1…v45） | — | 检索无结果 | — |
| 缺失值策略 | 部分实现 | 写在各版本配置里（如 v42 的 `NO_OBSERVED_NEWS/COLLECTION_GAP/...`） | — | 没有通用策略 | — |

## 3. 回测/评估

| 组件 | 状态 | 关键文件 | 入口/命令 | 依据 | 阻塞 |
|---|---|---|---|---|---|
| 事件级配对评估 | 已有且可运行 | `src/m5_bridge/evaluate.py`（Spearman、共同事件、attrition） | `python -m m5_bridge evaluate / verify-output` | `tests/test_m5_bridge_evaluate.py`，含合成数据模式 | 需要外部准备好的 CSV，自身不生成收益 |
| 时序切分 + 清洗重叠样本（purge） | 部分实现 | `scripts/news_sentiment_factor_v3/evaluate.py:70`（`fold_specs`，扩展窗口） | 一次性脚本 | `tests/test_news_sentiment_factor_v3.py` | 私有收益；是开发比较，不是样本外检验 |
| 收益窗口/标签 | 部分实现 | `v44/labels.py`；`market_observer_v1/evaluation.py`；`direction_baseline.py` | — | 有测试，但前瞻标签数为 0 | 标签要等到期 |
| 描述性价格状态研究 | 已有（已执行） | `research/market_sentiment/phase1/baseline.py`、`self_check.py` | 脚本 | `phase1/results/run_manifest.json` | 私有行情库 |
| 交易成本/换手 | **缺失** | 只有配置里的 `transaction_cost: NOT_ESTIMATED` | — | 没有任何实现 | — |
| 组合构建/PnL/回撤/Sharpe | **缺失** | `docs/news_research/roadmap.en.md:149` 只是计划 | — | 没有回测引擎 | — |
| 样本外隔离 | 部分实现 | v42 配置把 2026-04 至 07 的标签标为 `SEALED_NOT_READ`；M5 有 purge 设计 | — | 只靠配置和人工约束 | — |

## 4. 训练/模型

| 组件 | 状态 | 关键文件 | 入口/命令 | 依据 | 阻塞 |
|---|---|---|---|---|---|
| LLM 抽取/解读（Qwen2.5-3B，v15–v18.2，**多版本并存**） | 部分实现 | `scripts/quant_personal_feed/analysis_v15…v182_run.py`；`configs/news_extraction_v182_20261117.json`（固定模型版本，seed 1729） | `ops/analysis_personal.ps1`、`analysis_v182_run.py` | `test_quant_personal_v15*`…`test_news_extraction_v182.py` | 需要 GPU 和本地模型；每一版都是有上限的私有运行 |
| news_n1 / shortfacts / 约束解码 | 部分实现 | `run_news_n1_*.py`、`news_shortfacts_v1/v2`、`news_constrained_decode_v1/` | 脚本 | 有对应测试 | 一次性 smoke |
| 微调 | **缺失** | — | — | 检索无代码（命中均为误报） | — |
| 回归拟合 | 部分实现 | v3 `fit_candidate`；M5 的 Ridge 只是提案 | — | — | — |
| 实验追踪（**三套分散**） | 部分实现 | `results/*/plan_execution.csv`；`research/market_sentiment/record_step.py`（只追加，带证据哈希）；`market_observer_v1/store.py:175-225`（model_versions、predictions、outcomes 表） | `python record_step.py --entry x.json` | 没有统一的 run_id | — |
| 产物溯源/seed | 部分实现 | 配置和 manifest 记录 config/code/input 的 SHA-256 | — | `verify-output` 可复算（finembed、m5_bridge） | 只在各模块内部 |

## 5. 编排

| 组件 | 状态 | 关键文件 | 入口/命令 | 依据 | 阻塞 |
|---|---|---|---|---|---|
| 容器监督/角色 | 已有且可运行 | `deploy/linux/supervisor.py`、`compose.yaml`（db/web/proxy/feed/research/daily-price…） | `docker compose up` | `deploy/linux/verify_matrix.py`、`test_round3_linux.py` | 云端授权 |
| 定时 | 已有 | systemd：SEC 刷新、备份 | — | 只覆盖采集和备份，没有研究任务 | — |
| 作业队列（**三套以上，重复实现**） | 部分实现 | analysis v15/v16 的 jobs/attempts；v42 worker；v43 `daily_label_queue` | 各 worker 的 CLI | 每个 worker 自带队列 | 没有通用的研究作业运行器 |
| 门户结果页 | 部分实现 | `core/views.py:193` 的 `research_sentiment_json`（只读） | Django | `core/test_research_sentiment_v4*.py` | 没有实验、回测或因子结果页 |

## 明显的重复实现

- 价格获取有 6 处以上。
- SEC 导入有 3 套。
- 交易日历有 2 套。
- 作业队列有 3 套以上。
- 实验记录有 3 套。
- 评估器有 3 个。
- LLM 分析 v15 到 v18.2 共 8 个以上版本并存。

新建任何东西之前，应先在这些现有实现中选定一套沿用。

## 纵向工作流的主要缺口

纵向工作流指：版本化数据集 → 一个版本化因子 → 带成本的时序回测 → 实验记录 → 结果页。

1. **数据集版本层**：目前只有各版本自带的冻结哈希，没有 dataset_id 和 manifest 注册表。可以在 v42/v43 的快照和 `learning.build_temporal_manifest` 基础上统一。
2. **因子注册表**：缺失。最接近的现成信号是 v43 的 N1/N2 日快照，但合格样本为 0。
3. **回测引擎**：交易成本、换手、组合和 PnL 完全缺失。可复用 v3 的 `fold_specs`（时序切分 + purge）和 `m5_bridge evaluate`（事件级指标、`verify-output`）。
4. **统一实验记录**：缺 run_id。可以在 `record_step.py` 或 `market_observer_v1` 的 `model_versions/predictions` 表基础上扩展，不要再新建第四套。
5. **门户结果页**：缺失。可以沿用 `research_sentiment_json` 这种只读 JSON 模式。
6. **数据可得性**：真实运行依赖 moomoo、Massive 或 WRDS 授权和私有 artifacts。CRSP/Compustat 尚未入库（M5 仍为 `NOT_RUN`）。这是开始任何样本外研究之前的前置阻塞。
