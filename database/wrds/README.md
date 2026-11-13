# WRDS material catalog and local database

## What is available / 当前提供什么

This directory contains a real SQLite metadata database, not a live database server. It registers accessible WRDS/M5 candidate and supporting materials, source-file identities, field roles, reviewed engineering aggregates, and unresolved source definitions. Download `catalog.sqlite` to query it with SQLite or DB Browser for SQLite. The same records are available under `exports/` as JSON and CSV.

这里提供实际可查询的 SQLite 资料目录，以及 JSON/CSV 导出。GitHub 保存数据库快照与构建代码，不提供持续运行的 SQL 服务。原始 WRDS 行级数据仍应保存在符合订阅条件的受控本机位置。

The 14 November 2026 inventory examined 330 accessible project entries. It selected 215 attachment entries with a WRDS/M5 candidate or support role and retained 13 separately identified source-packet image references. **These 228 catalog rows are not 228 datasets.** Of them, 99 project screenshots have unreviewed database relevance. The 55 small acquired support materials include code and non-WRDS research support, not 55 original data exports. Their file hashes were recomputed; all 49 acquired ZIPs passed CRC and path checks. Six source archives additionally passed 163 manifest payload hashes. Original large export entries remain `LIBRARY_METADATA_ONLY` unless actual acquisition is recorded.

目录区分“实际取得并核验”“只有附件元数据”和“来源包中的引用”。同一字节内容的不同文件名会保留，`duplicate_contents` 视图只对本轮实际取得并哈希核验的文件识别重复。截图引用的哈希描述包内副本，不自动认证与聊天原图相同。

The current snapshot contains 13 dataset roles, 22 field-role entries, 35 source-evidence records, 43 reviewed aggregate results, 8 run/readback records, and 8 open source/research issues. A dataset-role row does not claim that the corresponding original data were loaded here. Source dates and evidence pointers remain attached to every aggregate.

## Quick start / 查询目录

From the repository root, use Python 3.11 or later. No additional package is required for this component. The actual synthetic checks in this publication ran on Python 3.12.14, not Python 3.11.

```bash
python -m wrds_catalog validate --db database/wrds/catalog.sqlite
python -m wrds_catalog query --db database/wrds/catalog.sqlite --sql "SELECT category, materialization_status, artifacts FROM catalog_counts ORDER BY category"
python -m wrds_catalog query --db database/wrds/catalog.sqlite --sql "SELECT dataset_id, metric, value, unit, as_of FROM aggregate_results ORDER BY dataset_id, metric"
python -m wrds_catalog query --db database/wrds/catalog.sqlite --sql "SELECT issue_id, status, description FROM open_issues"
```

Queries accept one read-only `SELECT` or `WITH` statement. Mutation, `ATTACH`, writable pragmas and file/extension functions are rejected. Results preserve positional columns, including duplicate column aliases, and report truncation.

## Rebuild / 重建快照

```bash
python -m wrds_catalog build --inventory database/wrds/materials_inventory.json --reviewed database/wrds/reviewed_evidence.json --db database/wrds/catalog.sqlite
python -m wrds_catalog export --db database/wrds/catalog.sqlite --out database/wrds/exports
python -m unittest discover -s tests -p "test_wrds_*.py" -v
```

The public catalog is built into a new file from an explicit field allowlist. It is never obtained by deleting columns from a private row database. The builder rejects private locations, account/transport identifiers, unknown schema columns and missing evidence relationships. Repeated builds from the same source JSON are byte reproducible.

## Local licensed data / 本机原始数据库

The private importer is implemented, but **no real WRDS source rows were imported in this cloud task**. Actual local population remains a separate step on the research machine with the retained exports. It reads saved files only and never logs into WRDS or submits a new query.

将原始 CSV 或原始 ZIP 保存在受控位置，进入仓库根目录后运行以下命令。将 `PATH_TO_ORIGINAL_EXPORT.csv` 替换为实际本机文件；ZIP 含多个成员时必须用 `--member` 指定确切 CSV。这里只导入有表头的 UTF-8 / UTF-8-BOM CSV。标识始终保留为字符串。

```bash
python -m wrds_catalog import --db .wrds_private/market.sqlite --source PATH_TO_ORIGINAL_EXPORT.csv --dataset-kind daily
python -m wrds_catalog import --db .wrds_private/market.sqlite --source PATH_TO_ORIGINAL_EXPORT.zip --member EXACT_MEMBER.csv --dataset-kind ccm
python -m wrds_catalog verify-import --db .wrds_private/market.sqlite
python -m wrds_catalog query --db .wrds_private/market.sqlite --sql "SELECT content_id, row_count, width_mismatch_rows FROM contents"
python -m wrds_catalog query --db .wrds_private/market.sqlite --sql "SELECT dataset_kind, COUNT(*) AS source_occurrences FROM source_occurrences GROUP BY dataset_kind"
```

`--dataset-kind` is a provenance label supplied by the operator, not a provider table certification. The importer preserves all positional raw strings, header duplicates and collisions, blank cells, unknown date text, row ordinals and width mismatches. Exact CSV content shares one content entity across transports, while each import keeps its source occurrence. It does not infer dates, choose a primary security, adjudicate history, deduplicate the research sample, adjust prices or generate return labels. Those semantics remain governed by the existing study scripts and accepted rules.

本机表包括 `contents`、`headers`、`records`、`source_occurrences`。`records.cells_json` 按原列序保存每行字符串；`headers.column_ordinal` 从 0 开始，数据行 `row_ordinal` 从 1 开始。原始文件不变，独立复核会重新读取保留源并核对每行、每个单元格的逻辑指纹。单文件导入失败会回滚，新的数据库原子发布；新库发布需要支持硬链接的文件系统。

Default private output must be inside `.wrds_private/`. An explicitly chosen external controlled directory requires `--output-root`. The public `database/wrds/` path is always forbidden for row-data imports. Do not commit private `.sqlite`, WAL, journal files, SQL identifier lists or raw archives. Adding a private repository or LFS does not establish a vendor redistribution right.

## Provenance and current research limits / 证据与边界

- `materials_inventory.json`: file-level metadata and explicit acquisition/classification status.
- `reviewed_evidence.json`: reviewed aggregate cells, exact archive/member hashes, source pointers and dated limitations.
- `schema.sql`: public catalog schema. `local_schema.sql`: private row-store schema.
- `queries.sql`: example read-only questions.
- `exports/`: the actual populated catalog in readable formats.
- `BUILD_RECEIPT.json`: actual commands, software checks and snapshot hashes for this publication.

Historical summaries and newer source updates are kept with their dates. The latest 14 November material still leaves source meanings and historical identity unresolved. Bounded filing discovery, structural interval intersections and zero admitted rows are not a formal cohort. Formal M5 remains `PROPOSED / researcher_accepted=false / NOT_RUN`; formal company/event cohort counts remain unresolved.

## Why original files are absent from Git / 原件为什么未上传

The existing repository is public. Its `SHARING_SCOPE.md` already excludes licensed exports, raw filings, account screenshots, support correspondence and private indexes. WRDS permits sharing code and other documents without passwords, while requiring vendor permission for proprietary/full-data sharing and private protection for off-platform data. Therefore this task publishes the directory, reviewed aggregates and build software, rather than mirroring source archives or licensed row databases.

Original attachments remain separate from this public snapshot. Full provider manuals and account captures are represented through source identities or official links, not copied wholesale. Source-request handoff documents are not treated as an actual WRDS response.

Official references, opened 14 November 2026:

- [WRDS off-platform download and analysis policy](https://wrds-www.wharton.upenn.edu/pages/about/data-download-and-analysis-policy/)
- [WRDS code/document and coauthor sharing guidance](https://wrds-www.wharton.upenn.edu/pages/about/navigating-co-authorship-across-institutions-a-guide-to-data-sharing-and-compliance/)
- [GitHub large-file and database guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)

This component adds no data license and accepts no M5 protocol version. No real encoder, financial training, scoring or backtest runs as part of catalog construction.
