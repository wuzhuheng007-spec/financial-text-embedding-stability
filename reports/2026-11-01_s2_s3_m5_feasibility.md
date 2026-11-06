# Round 7: saved S2/S3 synthesis and M5 financial-data feasibility

**Status:** HISTORICAL_READBACK_ONLY for S2/S3; LOCAL_AVAILABILITY_AUDIT_ONLY for M5; `PROPOSED / NOT_RUN` for the financial protocol. No new encoder, predictor, training, backtest or finance metric was executed in round 7. The saved five-company, one-model experiments do not validate a general representation method or a financial result.

## 1. Source and comparison contract

The complete machine synthesis is retained locally as `artifacts/round7/s2_s3_synthesis.json` (SHA-256 `20c57059d353122f90449a53a9839559b7fad0f904d913ad103078a74c374b10`). It was computed from the saved S2 manifest/metrics SHA-256 `698942ddc8a7b395108a3ce2b72deab0f370ed3cd9de2bca827b693632182d5e` / `879b9753af91e8f1a1a9248aec4b81577a16c2ac902b33b8b63da3b148d2ac6d` and S3 manifest/metrics SHA-256 `eab4abec64f65d9f9d48589eba20c59edc5c75f4d6ee2567db6765e58dfd1f47` / `77e4bcdd7074a0206ccf798e4770106177b2409c57ecef02a7ef3d51b0b22f27`. Both use input-manifest SHA-256 `c240d12864bdbe95f21586d4068218a2d3623e1372ba403d521a01fdfdb82a4e`, the same ordered five Item 1 hashes, pinned MiniLM/ONNX bytes, CPU provider, batch size 8 and normalization order. S2 length 128 and S3 shift zero matched 15/15 saved chunk/company arrays byte-for-byte, all five chunk maps, and both full reference-arm descriptions. This is reference compatibility, not an independent replicate or the colleague's missing result.

| Protocol | Fixed factors | Changed factors | Saved design and execution | Interpretation |
| --- | --- | --- | --- | --- |
| S2 | Five exact complete Item 1 texts, tokenizer/encoder, CPU, zero overlap; pooling fixed within each length contrast | Body-token length 118/123/128/133/138, many internal boundaries, chunk count and tail length | Ten length×pooling arms; 25 jobs, 1,083 chunk vectors, 50 company vectors, 145 batch attempts; 8 same-pooling length contrasts, 5 same-length pooling contrasts, 40 signed pair interactions | Descriptive combined processing change; no pure token-count effect |
| S3 | The same texts/model/provider, 128-token base step, 216 chunks per shift, complete coverage; pooling fixed within each shift contrast | Internal cut displacement -10/-5/0/+5/+10, chunk contents, first/tail lengths and body-token-weighted weights | Ten shift×pooling arms; 25 jobs, 1,080 chunk vectors, 50 company vectors, 145 batch attempts; 8 same-pooling shift contrasts, 5 same-shift pooling contrasts, 40 signed pair interactions | Descriptive combined boundary/first-tail change; no pure semantic-boundary effect |

Pooling contrasts within each document-setting task reuse its *same stored chunk vectors*. Equal-chunk weights remain `1/n`; S3 body-token-weighted first/tail weights change with shift. S2 and S3 are parallel **descriptive** families and must not be subtracted to attribute causal contributions to boundaries, chunk count or tails. Five firms yield ten dependent upper-triangle company pairs, not ten independent samples. The S3 design followed inspection of S2 on the same texts; it is not independent confirmation.

## 2. All saved settings and same-pooling reference contrasts

The setting column is a **body-token length** for S2 and an **internal boundary shift** for S3. Each nonreference row compares with the same-pooling S2 length-128 or S3 shift-zero arm. Mean vector cosine distance averages five firms; mean absolute pair-cosine change and Spearman use ten dependent company pairs; Top-2 set-change rows count firm/arm changes. Gap is the mean score difference between each firm's rank-2 and rank-3 peers. A reference row has no self-effect statistic.

| Protocol | Setting | Pooling | Mean vector distance | Mean absolute pair change | Pair Spearman | Top-2 set changes | Mean rank-2/3 gap |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| S2 | 118 | equal_chunks | 0.00403765517 | 0.00905742398 | 0.939393939 | 0 | 0.0298633623 |
| S2 | 118 | body_token_weighted | 0.00397103314 | 0.00797332184 | 0.951515152 | 0 | 0.0321588417 |
| S2 | 123 | equal_chunks | 0.00389215948 | 0.0121529131 | 0.854545455 | 1 | 0.0334025529 |
| S2 | 123 | body_token_weighted | 0.00377874538 | 0.0120466934 | 0.878787879 | 1 | 0.0354551806 |
| S2 | 128 | equal_chunks | reference | reference | reference | reference | 0.0289253737 |
| S2 | 128 | body_token_weighted | reference | reference | reference | reference | 0.0301746957 |
| S2 | 133 | equal_chunks | 0.00442901258 | 0.00927975315 | 0.866666667 | 1 | 0.0326618311 |
| S2 | 133 | body_token_weighted | 0.00393568578 | 0.00691559625 | 0.951515152 | 0 | 0.0337421562 |
| S2 | 138 | equal_chunks | 0.0048795072 | 0.0094741234 | 0.890909091 | 1 | 0.0364034562 |
| S2 | 138 | body_token_weighted | 0.00447921791 | 0.00758092277 | 0.975757576 | 0 | 0.0358910727 |
| S3 | -10 | equal_chunks | 0.00260533893 | 0.00479403471 | 0.939393939 | 0 | 0.0298375543 |
| S3 | -10 | body_token_weighted | 0.00252607786 | 0.00480049512 | 0.951515152 | 0 | 0.0306500722 |
| S3 | -5 | equal_chunks | 0.00199287402 | 0.00390541441 | 0.987878788 | 0 | 0.0284704398 |
| S3 | -5 | body_token_weighted | 0.00187747827 | 0.00404201548 | 1 | 0 | 0.0299665728 |
| S3 | 0 | equal_chunks | reference | reference | reference | reference | 0.0289253737 |
| S3 | 0 | body_token_weighted | reference | reference | reference | reference | 0.0301746957 |
| S3 | +5 | equal_chunks | 0.0017219882 | 0.00529353894 | 0.987878788 | 0 | 0.034741129 |
| S3 | +5 | body_token_weighted | 0.00167937599 | 0.00507403858 | 0.963636364 | 0 | 0.035349301 |
| S3 | +10 | equal_chunks | 0.00216522066 | 0.00471439637 | 0.987878788 | 0 | 0.0332058066 |
| S3 | +10 | body_token_weighted | 0.00211129605 | 0.00450632795 | 0.951515152 | 0 | 0.0340882402 |

S2's four same-pooling Top-2 set-change rows were all MSFT: length 123 under both pooling arms, and lengths 133 and 138 under equal pooling. S3 had none. A retained Top-2 *set* does not mean an unchanged vector, cosine score, pair ordering or rank-2/3 gap; the complete machine results retain all of them.

## 3. Pooling contrasts and signed interactions

Each pooling row is weighted minus equal at a fixed setting. Each interaction is the mean of ten **signed pair** values: `[S(setting,weighted)-S(reference,weighted)] - [S(setting,equal)-S(reference,equal)]`. These differences are descriptive, not causal stabilization or information-retention estimates. The machine synthesis retains all 80 individual signed pair-interaction rows across the two protocols.

| Protocol | Setting | Pooling mean absolute pair change | Pooling pair Spearman | Pooling Top-2 set changes | Mean signed interaction versus reference |
| --- | ---: | ---: | ---: | ---: | ---: |
| S2 | 118 | 0.00156106743 | 1 | 0 | +0.00153663669 |
| S2 | 123 | 0.0011505113 | 1 | 0 | +0.00108801809 |
| S2 | 128 | 0.00198562669 | 0.987878788 | 0 | reference |
| S2 | 133 | 0.00226104428 | 0.951515152 | 1 | +0.00256338458 |
| S2 | 138 | 0.00231519349 | 0.963636364 | 1 | +0.0030019102 |
| S3 | -10 | 0.00134276466 | 1 | 0 | +0.000493589367 |
| S3 | -5 | 0.00159045661 | 1 | 0 | +0.000101026191 |
| S3 | 0 | 0.00198562669 | 0.987878788 | 0 | reference |
| S3 | +5 | 0.00199694661 | 0.963636364 | 0 | -5.91584015e-05 |
| S3 | +10 | 0.00201160891 | 0.987878788 | 0 | -0.000181777952 |

S2 had two fixed-length pooling Top-2 set-change rows (MSFT at 133 and 138); S3 had zero. Both saved runs had **zero failures, zero excluded company-arms, zero undefined Spearman comparisons and zero exact rank-2/3 cutoff ties**. No observed arm is designated a default. The lower S3 changes under this grid cannot identify the individual effects of boundaries, chunk count, first/tail weights, or semantic quality.

## 4. Current local financial-data and time audit

The machine-readable current-file receipt is `artifacts/round7/m5_data_audit_v5.json`, SHA-256 `eab463172225170ab935de5599e258f916576de712e5434a1bfa929ebc9d391e`. Earlier v1–v4 local audit drafts remain preserved; v5 adds the previously omitted M7 AAPL quarterly pilot. The [audit code](../scripts/audit_m5_data_round7.py) rehashed original M1 submission/Item 1 bytes, M4 processed bars and raw response receipts, saved company-identity files, the M4 manifest/config/common mask, M7/M8 original quarterly sources, and M9 transformed versions without computing a return label. M1 corpus-index SHA-256 is `ce54c348458e2c90abee0435f72fe84ff3895e7695c2a2f4ed212eb61035a18c`; M4 manifest/config/common-mask SHA-256 are `c4815bcbeafea98e3810cb3f46849e6c1b544bd2e5beb3a4835a5fddb36ca755`, `a09317baf128cfb3dc446c140d4e36018f40904cc824018d93b4345dd0222535`, and `4f4a0f4b6a7d0c57bd56a9c0079520d662eb58423b90ad9a7566a86393a9c647`.

| Unit or material | Current-file count or span | Interpretation |
| --- | --- | --- |
| Companies / independent original annual 10-K filings / independent Item 1 SHA-256 values | 5 / 5 / 5 | One annual development text per company; no multi-filing train/validation/test panel |
| Physical Item 1 copies across saved runs/snapshots | 15 | Copies collapse to the same five source hashes; not extra independent documents |
| M7/M8 original 10-Q complete submissions | 1 M7 AAPL plus 4 M8, five distinct accessions | Quarterly source stratum; saved extraction/review statuses differ; not annual Item 1 inputs |
| M9 structured Item 2 derivatives | 2 distinct text hashes in 8 physical copies | Transformed quarterly derivatives, not original filings or additional annual texts |
| Massive daily-bar dates | 250 shared dates, 2025-01-02 through 2025-12-31 | One saved calendar year only; no bound independent point-in-time exchange calendar |
| Stock-date bars / saved close-return-mask rows | 1,250 / 1,245 | Price observations and a historical price-only return mask, not filing-event sample sizes |
| Five-session post-filing close pairs from saved dates | 5 structurally available | Date/row existence only; no label value or approved prediction event was computed |
| Approved financial prediction observations | `null` | Public-time, decision-time text vintage, label, universe, split and missingness policies remain unapproved |

Every retained daily bar has fields `t,o,h,l,c,v,vw,n,date_et`, with zero missing values in those required fields and an Eastern date consistent with its timestamp. M4 requested Massive REST daily aggregates with `adjusted=true`; its stored receipt places retrieval in October 2026, well after the 2025 bars. The [provider's FAQ](https://massive.com/knowledge-base/article/is-massives-stock-data-adjusted-for-splits-or-dividends) states these aggregate bars are **split-adjusted, not dividend-adjusted**, and a later download can revise the historical adjusted view after a subsequent split. There is no local dividend-cashflow series or delisting-return history bound to this dataset. The five saved reference identity records match their SEC CIKs for their own `as_of` query, but do not establish an all-date ticker/security chain or a no-survivor-selection universe. A saved weekday comparison exists; a version-bound independent exchange trading calendar does not.

The following source dates and structural future-close dates come from the rehashed local audit, **not** from a financial return calculation. All Item 1 SHA-256 values are in the machine receipt and in the existing S2/S3 result reports.

| Firm | Original 10-K accession | Report period end | Official filing date | First saved bar strictly after filing | Fifth subsequent saved bar |
| --- | --- | --- | --- | --- | --- |
| AAPL | `0000320193-25-000079` | 2025-09-27 | 2025-10-31 | 2025-11-03 | 2025-11-10 |
| MSFT | `0000950170-25-100235` | 2025-06-30 | 2025-07-30 | 2025-07-31 | 2025-08-07 |
| AMZN | `0001018724-25-000004` | 2024-12-31 | 2025-02-07 | 2025-02-10 | 2025-02-18 |
| GOOGL | `0001652044-25-000014` | 2024-12-31 | 2025-02-05 | 2025-02-06 | 2025-02-13 |
| META | `0001326801-25-000017` | 2024-12-31 | 2025-01-30 | 2025-01-31 | 2025-02-07 |

The raw SEC header and saved submissions API carry acceptance fields, but they do not prove the first public instant. The audit rehashed the currently saved submissions snapshots and found one same-period original 10-K row and zero same-period 10-K/A rows for each firm **within those snapshots**; this does not rule out a post-acceptance correction or establish the exact decision-time Item 1 bytes. The [SEC FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions) describes a variable lag from acceptance to sec.gov availability and no recorded first-public timestamp; [SEC archive guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) discusses delayed dissemination and corrections. Acceptance, first public availability and a usable market decision instant are distinct. The 2026 download time identifies the saved snapshot, not the first availability of a 2025 filing.

The extra quarterly originals are M7 AAPL accession `0000320193-25-000008` (2024-12-28 period, source SHA-256 `297258ba2ef1211b8cc6efdbf847ac127bd8cf39e15421ddde7e3aaf5200ffa4`) and four M8 MSFT/AMZN/GOOGL/META accessions recorded in the v5 audit. M7's final pilot extraction artifact reports programmatic boundary pass with human review then pending; subsequent review decisions are separate evidence. M8's four source records have their own version-review status. The M9 AMZN/GOOGL Item 2 structured files are transformed versions of two of those quarterly filings. None changes the five original annual Item 1 count or supplies the proposed M5 historical panel.

The present five texts and one year of prices cannot fill the proposed 2020–2023 training, 2024 validation and 2025 test filing-event splits. A larger source inventory, historical security master, verified section vintages, daily prices extending before each feature lookback and beyond each label horizon, delisting policy and session calendar are required. Repeating one annual filing vector on many subsequent daily rows would increase row count without increasing independent text versions; the candidate therefore uses one event row per original filing.

## 5. Exact model-time evidence and interpretation

| Object | Verified official evidence | Supported timing or limit |
| --- | --- | --- |
| Pinned repository revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` | [Official commit diff](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/commit/1110a243fdf4706b3f48f1d95db1a4f5529b4d41) and [history](https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2/commits/main) | 2026-06-01 06:29:13 UTC; README metadata change, not proof the weights were newly released then |
| Pinned ONNX SHA-256 `6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452` | [Official ONNX addition commit](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/commit/bc57282bc374d33e0d6c4de27f12dc1c2a87f37a) and local hash | Exact bytes present by 2024-10-10 18:08:32 UTC |
| Pinned tokenizer SHA-256 `be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037` | [Official expanded tree/file history](https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41?recursive=true&expand=true) and matching local Git blob ID | Exact blob present by 2021-08-30 19:05:43 UTC |
| Original weight and training data | Same official file history; [model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md) describes fine-tuning pairs | Original weight file dates to 2021; global base-pretraining/fine-tuning data cutoff is `UNKNOWN`; a cited 2015–2018 subset is not a global cutoff |

The exact ONNX and tokenizer bytes have pre-2025 file-history evidence, while the currently named repository revision itself was created in 2026. A strictly historical 2025 forecast claim would need a contemporaneous complete revision or byte manifest, established training-knowledge boundary, decision-time filing bytes and public availability, plus market-data vintage and execution rules. Those gates are not satisfied by a model release date or by SEC acceptance alone. A later fixed-model **retrospective association** can be separately studied, with this limitation disclosed; it cannot silently become a no-look-ahead historical strategy. The ONNX addition in the same official repository does not by itself prove numerical identity with the original PyTorch weights. These time gaps do not alter the validity of the already saved fixed-input engineering computations.

## 6. Proposed minimum M5 experiment and resource envelope

The maintained [M5 protocol draft](../docs/M5_PROTOCOL_DRAFT.md) and [machine-readable candidate](../configs/m5_financial_protocol_round7.json) make the method explicit without freezing it. The recommended planning cohort is up to 200 historically identified CIKs selected by the candidate's fixed hash rule from an as-of-2020-01-01 operating-common-share list, including later exits and identifier changes and excluding the five S2/S3 development CIKs. Original annual 10-K Item 1 filing events with decision years 2020–2023 / 2024 / 2025 form training / validation / untouched test. The exact eligible row counts are `null` until that universe and source data exist. This cohort defines a bounded **2020 cohort**, not all future-listed U.S. equities or a power guarantee.

The main proposed target is five-subsequent-session split-adjusted **closing-price** return after a post-filing 18:00 Eastern decision conditional on availability of the exact text and finalized decision-day bar; 21 sessions and dividend-inclusive total return are alternatives needing more price/corporate-action coverage. `F0` uses trailing-price features; `F1` adds train-only TF-IDF/SVD lexical coordinates; `F2` adds train-only PCA coordinates from the pre-existing 128/equal dense reference. All three share event rows, labels, chronological purging **before validation selection**, ridge predictor and validation tuning budget. Primary held-out score is common-row `1-SSE(F2)/SSE(F0)`; other comparisons and paired MAE/rank measures are secondary. This predicts a price label retrospectively; it does not assume a fill at the reference close or calculate net economic benefit. A trading evaluation requires separate rebalancing, executable price, spread, fees, slippage, financing, corporate-action and turnover rules before any result is seen.

At one filing per firm-year, 200 firms×6 years would be 1,200 filing events **before attrition**, only an illustrative workload and neither an observed count nor a hard cap. The five current 128-token texts contain 216 chunks, a sample mean of 43.2 per text; applying that mean to 1,200 texts illustrates 51,840 chunks, not a forecast of new corpus length, runtime or peak memory. One 1,200×384 float32 filing-vector matrix would occupy 1,843,200 bytes before chunks, sparse lexical features, model files or metadata. Three arms×three alpha candidates plan nine validation fits plus three post-selection refits. A future tokenizer-only preflight and measured CPU pilot must replace these illustrations. School GPU type, allocation, quota and queue remain `UNKNOWN / NOT_VERIFIED`; this minimum proposal does not presume GPU access.

## 7. Ordered decisions for the researcher

The choices below are recommendations, **not** S3 acceptance and not M5 freeze. Data evidence is stated separately from the scientific choice. Unresolved required decisions block the named future step; none blocks this round's source readback.

| Priority / decision | Recommended choice | Alternative and effect | Current evidence | Blocks |
| --- | --- | --- | --- | --- |
| 1. Claim/time regime | Retrospective fixed-byte association with explicit 2026 revision-label and unknown-training-cutoff limits | Strict historical-as-of forecast requires a contemporaneous complete model manifest, training cutoff, text/publication and market-vintage proof | ONNX/tokenizer bytes predate 2025; exact revision label is 2026; full cutoff unknown | Claim language and any as-of execution |
| 2. Cohort and source versions | Bounded 2020 as-of cohort of up to 200 CIKs by fixed hash, original annual Item 1, five development CIKs excluded; retain exits | All eligible historical firms increases data and compute; a current-survivor list changes target population and risks selection bias | Locally only five independent annual texts; historical security master absent | Filing-event inventory and split counts |
| 3. Public and decision clock | 18:00 Eastern on first regular session strictly after official filing date, conditional on exact public text and finalized close bar by then | Earlier open needs stronger dissemination evidence; a later lag reduces usable immediacy and may change target | Acceptance and filing fields saved; first-public instant, PAC/content vintage and close-bar finalization time unknown | Feature eligibility and event timestamp |
| 4. Target and horizon | Five-subsequent-session split-adjusted price return; disclose no dividends and non-executable reference close | 21 sessions needs longer label coverage; total return requires dividends, delisting and reinvestment policy | One saved 2025 price year and five structural close pairs, no approved labels | Label construction and primary metric |
| 5. Market identity/coverage | Versioned regular-session calendar, point-in-time CIK/security chain, price and exit coverage through each feature/label window | Restrict to surviving five firms only for an explicitly narrower smoke, not formal cohort evidence | Current CIK matches are snapshot-only; delisting and PIT calendar evidence absent | Eligible common sample and failure policy |
| 6. Predictors and text reference | Matched F0/F1/F2 ridge arms, 32 added text coordinates, 128/equal historical dense reference, three fixed alpha choices | Other lexical/dense dimensions or weighted pooling require a separately frozen sensitivity arm; S2/S3 outcomes cannot select a winner | M4 PCA/TF-IDF are descriptive demos, not forecast baselines | Feature generation and validation tuning |
| 7. Chronology and score | Train decision years 2020–2023, validate 2024, test 2025; purge crossing labels; primary common-row squared-error reduction | Later test years may support a strict-as-of design after evidence; monthly horizon requires new purge/coverage | Current one-year prices and one text per firm cannot support these splits | Held-out prediction evaluation |
| 8. Economic extension | Keep net trading value out of this candidate | A separate portfolio protocol must freeze orders, execution, turnover, costs, financing and actions before backtest | No saved trading-cost/implementation receipt | Economic or trading claim only |

## 8. Validation, limits and open evidence

`scripts/audit_m5_data_round7.py` was run only against already saved local files; its six selected synthetic tests covered altered source bytes, changed price order, missing prices, structural counts and source identity. The verified Python 3.11 interpreter used this checkout's `src` through explicit `PYTHONPATH`; a private import-identity record SHA-256 `3d28d91f9a7d1fdefcab49269387fcbbfa4c4e11e187b852a906129fe3294504` lists actual interpreter/module files and hashes. The audit code SHA-256 `0e7fab2818804ca5ef9ef0e10572111799958cbe2d41f50e57a3c4e1a9b4a5b6` equals the value bound inside v5. The commands below show verified entry points with protected local path arguments redacted; the actual unredacted input hashes are in v5.

```powershell
python scripts/audit_m5_data_round7.py --corpus <protected-M1-index> --m4-run <protected-M4-run> --m4-config configs/m4_demo.yaml --calendar-comparison <protected-M4-calendar-comparison> --text-inventory-root <protected-project-artifacts> --sec-cache-dir <protected-SEC-cache> --m7-root artifacts/m7/20261024T223714Z-tail-bm25-10q --m8-root artifacts/m8/20261025T230645Z-four-10q --m9-root artifacts/m9 --output artifacts/round7/m5_data_audit_v5.json
# exit 0; AUDIT_ONLY companies=5 texts=5 price_rows=1250
python -m pytest -q tests/test_audit_m5_data_round7.py
# exit 0; 6 passed; ignored log SHA-256 50f86a3d78221e11a64f454a79767a6315e7c329d6ebdb43063ec086ff5e066e
python artifacts/round7/validate_round7.py
# exit 0; saved S2/S3 hashes, setting/pooling tables, proposed config and local links passed; ignored log SHA-256 18576f853a559e6fdecb1b5d24f0c044abf0f4c4db0b8467280f8ffe92ff5f98
```

The first table-validation attempt used the interpreter's default GBK decoding on an English UTF-8 report and failed before validating rows. A second attempt exposed one equivalent scientific-notation presentation of a signed interaction; the displayed number was aligned to the machine format. A separate read-only science review caught a future decision-close finalization gap and a validation-label leakage path; both were corrected in the proposed protocol before submission. The final saved validator above passed. These were reporting/protocol checks, not model or financial runs. JSON/config syntax, Markdown links/tables and source hashes passed; staged-path and ZIP privacy/integrity checks are performed at local handoff. S2/S3 values in this report are **historical saved-result readbacks**. Financial prediction observations remain `null`; no target returns, trained predictor, out-of-sample score, positions or backtest were produced.

The round-3 accidental real-Encoder tests on synthetic text and the transient private quote in an unpushed round-4 intermediate commit remain historical deviations; no quote is copied here and no history is rewritten. The colleague's small-token supporting run package remains **not located**, the unspecified JFE paper identity remains **BLOCKED**, and official school GPU allocation evidence remains **NOT_VERIFIED**. These are separate missing items. Neither S2 nor S3 acceptance freezes M5, and none of the five-company engineering results establishes finance or economic value.
