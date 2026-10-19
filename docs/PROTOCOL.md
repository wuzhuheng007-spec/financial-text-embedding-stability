# Formal research protocol — unresolved

This file intentionally does not select final research parameters. The M2–M3 smoke pipeline fixes engineering settings in `configs/smoke.yaml` and `M2_M3_METHOD.md`; those choices are not a formal study protocol. Before any general or financial claim, freeze and justify:

1. Company universe, filing years, exclusions, filing amendment policy and point-in-time availability lag.
2. Text representation families, model/version hashes, preprocessing, dimensionality, and stability metrics.
3. Price-only baseline definitions, market data vendor and version, horizons, feature timestamp and splits.
4. Multi-model, multi-dataset and multi-seed design, uncertainty estimates and leakage checks.
5. Every numeric result's raw output, run configuration, code revision and input hashes; every citation's identity and claim support.

The smoke config is for ingestion engineering only.

The scientific decisions and conditional price-PCA K rule are in `M5_PROTOCOL_DRAFT.md`; the mainline/extension gates are in `M5_TRACKS.md`. Fixed-text representation stability is recommended as the first primary task; all formal choices remain unapproved and no M5 formal experiment has been run. The corrected five-company chronology is in `../reports/2026-10-19_text_price_time_alignment.md`: 2025 original filing identities are verified, but exact first-public timestamps and decision-time text identity are not. A 2026 download is snapshot provenance, not evidence that a 2025 filing was unavailable then. The 2025 price-window role is unassigned.

