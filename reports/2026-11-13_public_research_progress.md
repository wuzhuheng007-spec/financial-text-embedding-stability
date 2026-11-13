# Research progress, 13 November 2026

S2 and S3 have completed their fixed-input engineering runs and saved-output recomputation. Each contains 25 encoding tasks and 50 company vectors across ten settings. S2 saved 1,083 chunk vectors; S3 saved 1,080. Each run recorded one CPU encoder session and 145 batch attempts with no failed task. These are historical run counts, not computations performed for this publication. Exact run, input and metric identities are in the complete [S2](2026-10-31_small_chunk_probe_descriptive_results.md), [S3](2026-10-31_boundary_shift_probe_descriptive_results.md) and [synthesis](2026-11-01_s2_s3_m5_feasibility.md) reports and the machine results under `results/s2_round4/` and `results/s3_round6/`.

S2 varies length, boundaries, chunk count and tails together. S3 holds chunk count but changes content allocation, first/tail lengths and length-based weights. Pooling comparisons reuse each task's chunk vectors. The results support descriptive comparisons for these texts and this model. They do not establish general stability, semantic improvement, a causal mechanism or financial value.

## Data received and admission work

The [intake reports](../docs/M5_DATA_ADMISSION_READINESS_20261112.md) cover the starting security set, subsequent security and issuer histories, CIK-CUSIP observation candidates, eight Daily segments, bounded Distribution exports and filtered exits. The [attribute inventory](2026-11-13_m5_existing_attribute_inventory_handoff.md) records 7,639 starting securities and 29,404 issue intervals. These are source units, not admitted companies or financial events. The [CCM intake](2026-11-13_m5_ccm_query11905330_intake.md) adds a complete saved candidate-relation index with a separate readback check.

The [latest source comparison](2026-11-13_m5_ccm_linktype_source_review.md) found zero LO matches among four previously identified LINKTYPE exceptions. Their actual code meaning remains unresolved. The account page's selected values and a successful export do not prove the backend SQL, JOIN conditions or exact data release. Header CIK and primary-issue observations cannot automatically inherit CCM link dates.

Official documentation was consulted on 13 November. The [current CRSP guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_Compustat_Merged_Database_Guide_for_SAS_ASCII_R.pdf), [Legacy guide](https://www.crsp.org/crsp_pdf/crsp-compustat-merged-database-guide/) and [SAS special-missing documentation](https://support.sas.com/documentation/cdl/en/lrcon/62955/HTML/default/a000992455.htm) provide general definitions but do not identify this export's exact conversion or release. A request to WRDS Support is open. The account-specific ticket and correspondence remain private; no official answer has been received in this publication record.

## Proposed financial study

The [current candidate](../configs/m5_protocol_company_proxy_conditional_20261113.json) keeps company filing events, evidence-based historical security links and a documented primary issue. Its clock is a retrospective proxy based on the first applicable session strictly after SEC Filing Date. It does not claim that the text and finalized price were available to a historical trader at that time.

The remaining decisions concern the dated CIK/primary-issue bridge and market calendar; original filing and Item 1 versions; traded-close adjustments, distributions and exits; duplicate and missing-value treatment; and an exact full-protocol acceptance. The [remaining-requirements table](../docs/M5_COMPANY_PROXY_REMAINING_GATES_20261113.md) assigns each missing item to the execution step it blocks. Formal company and event counts remain null. M5 is PROPOSED, researcher_accepted=false and NOT_RUN.

## Integrity record

The earlier accidental real-encoder regression tests on synthetic text remain a recorded deviation with an unknown exact forward count. A private quote once appeared in an unpushed intermediate commit; that history is excluded from this publication. No prior evidence or candidate bytes were changed to conceal either event.

This synchronization copies source, tests, selected aggregate results and research documents. It does not rerun old audits, recompute labels, train, score or backtest. The public snapshot has a separate parent in the existing sharing history, and original development files remain local. Editorial changes follow the humanizer skill: direct descriptions, fewer repetitive qualifications, and no changes to supported claims or numerical evidence.
