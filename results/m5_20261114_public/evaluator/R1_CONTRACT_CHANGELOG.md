# R1 评估契约修复记录（2026-11-14）

范围：仅 `src/m5_bridge/evaluate.py`、`tests/test_m5_bridge_evaluate.py` 和独立 after-probe 脚本。所有本轮运行均为**合成契约验证**；没有真实价格标签、模型拟合或正式金融结论。

## 计划与实际逐项对应

| 计划 | 实际交付 | 验证 |
| --- | --- | --- |
| F1：时区感知的时间顺序 | 严格区分 ISO 日期与带时区时间戳；时间戳转 UTC 后比较；拒绝 naive 时间戳及无明确依据的日期/时间戳混比；市场可用日按绑定时区换算 | 跨时区、同一瞬时、DST、混合类型及 naive 测试；after-probe 中跨时区实际结果为 `false` |
| F2：第一个严格后续交易 session | 配置绑定单一 calendar ID、版本、市场时区、完整性和 coverage 文件；可纳入事件必须以 filing 后第一个 session 为 entry，exit 为 entry 加期限个 session；排除事件可保留空端点 | 延迟 entry、截断 coverage、calendar ID 错误均被拒；排除记录仍参与 attrition |
| F3：信号来源与真实训练窗口 | 每个 arm 声明 `signal_origin`；supervised 行绑定 fit manifest 与 SHA-256 校验的训练输入 manifest，从后者逐行计算 `label_end` 最大值并与 fit 声明对账，再检查 cutoff 与 transform 窗口；zero_shot/unsupervised 需明确 N/A 原因 | 训练源缺 `label_end`、训练 manifest 篡改、fit 声明篡改被拒；三类合法来源通过 |
| F4：市场相对收益绑定基础 target | `market_relative` 引用同 event、期限、窗口的已保存 raw/total 基础 target；绑定基准序列、收益约定、输入版本、窗口和固定 beta；`provided` 需来源 manifest/hash | 游离 `stock_target`、窗口不一致和 target 算式错误被拒；合法 raw/total 基础 target 通过 |
| 可选原输入复核 | `verify-output --inputs` 重新校验原输入及外部日历、训练、来源哈希，并独立重建 event/signal/target join；无 `--inputs` 标为内部一致性级别 | 合成 CLI 运行：24 条 event score、7 条 pair；input-bound 回读通过；修改输入会被拒 |

## 最终执行证据

- `PYTHONPATH=src python -m pytest tests/test_m5_bridge_diagnose.py tests/test_m5_bridge_evaluate.py -q`：退出码 0，**48 passed in 5.28s**。日志：`r1_tests_final.log`。
- `PYTHONPATH=src python -m m5_bridge evaluate --events synthetic_v2_final/inputs/events.csv --signals synthetic_v2_final/inputs/signals.csv --targets synthetic_v2_final/inputs/targets.csv --config synthetic_v2_final/inputs/config.json --output synthetic_v2_final/run`：退出码 0。日志：`synthetic_v2_final/evaluate.log`。
- `PYTHONPATH=src python -m m5_bridge verify-output --run-dir synthetic_v2_final/run --inputs synthetic_v2_final/inputs`：退出码 0，`verification_level=input_bound_independent_join`。日志：`synthetic_v2_final/verify_input_bound.log`。
- `python scripts/probe_m5_bridge_after_20261114.py --inputs artifacts/m5_next_20261114_0325/synthetic_v2_final/inputs --work-dir artifacts/m5_next_20261114_0325/after_probe_cases_final --output artifacts/m5_next_20261114_0325/after_probe_final.json`：退出码 0，`status=PASS`。control 接受；delayed anchor、缺训练 label_end、游离 market stock 均拒绝；跨时区比较与真实先后相符。

## 版本与限制

- 修复后的输入契约为 `schema_version=2`。旧 synthetic v1 fixture 需要迁移后才能被新的 `evaluate` 接受；旧 v1 输出的内部一致性回读仍可通过，但不代表满足 v2 契约。
- 日历 coverage 文件当前证明提供者声明的完整性与绑定范围；真实日历的来源、版本、市场适用性须另行核验。
- `verify-output --inputs` 是**原输入绑定的独立 join 回读**，其 schema 加载器与主评估器共用代码；不能称为完全独立实现。
- 固定 beta 已实现；估计 beta 的训练窗口绑定未实现，配置中会明确拒绝。真实金融运行仍受数据/协议门槛阻断。
- `r1_code_changes.patch` 为先前回传 ZIP 的 `code/` 路径到本轮文件的归档差异，不是针对当前 Git 索引的提交。

## 冻结标识

- `evaluate.py` SHA-256：`c36e3d33a34b35f9b10aea267ce8c9f98dc71f8fbfceee83c35ea62b41916142`。
- `tests/test_m5_bridge_evaluate.py` SHA-256：`788109361beecbf844bfa45ad325ef239a919963955b21fa602aa7c3acd63d41`。
- `scripts/probe_m5_bridge_after_20261114.py` SHA-256：`4b306aea02df355d01b0cd0b37eab0011e0028a0124535ba00886dbfc7a3fbe6`。
- `r1_code_changes.patch` SHA-256：`7753b247522b315d4743b83414890f6addbbecda75456aa42dd6cb91f2c058c8`。
- `synthetic_v2_final/run/run_manifest.json` SHA-256：`bebb8754d2cbec2205d1f5892994d8b815cfa688325bb9d6b441a123e082361d`。
