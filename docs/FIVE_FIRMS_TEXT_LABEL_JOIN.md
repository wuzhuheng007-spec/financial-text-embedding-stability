# Five-filing text/price-label identity check

**Scope:** `ENGINEERING_ONLY`. This local checker does not freeze M5, certify
historical tradability, train a predictor, score a model, or backtest. The five
development filings are not a formal 2020 cohort. No result from this check
establishes that a vendor file was licensed or that its historical security
mapping and adjustment method are independently certified.

Double-click `run_five_firms_text_label_join.cmd` in the repository root. The
launcher uses the pinned Windows Python 3.11 executable and this checkout's
`src` on `PYTHONPATH`. The script verifies the actual `finembed` module paths.
It never constructs an Encoder or ONNX session. Its only successful terminal
line is `WRDS_FIVE_FIRMS_TEXT_LABEL_JOIN_PASS`; otherwise it emits a redacted
`JOIN_FAIL CATEGORY:CODE` and exits nonzero. The launcher keeps the window open
until a key is pressed. Do not send label, price, PERMNO, or index files to
GitHub.

## Local inputs and file dialogs

Files are processed only when the user launches the command on the local
machine. The first known location is the user-identified
`E:\five_firms_wrds_pilot_labels` folder; the historical security checker
is in `E:\five_firms_wrds_local_check`. A dialog opens if a unique input
file is unavailable; canceling fails without output. The public SEC inventory
and round-4 manifest are checked first and normally found automatically. If
either is absent, its dialog appears before the private pilot-file dialogs.
The private pilot-file dialog order is:

1. `five_firms_price_label_pilot.csv` in the E: pilot folder.
2. The daily price CSV hashed by every label row. The producer recognizes
   `u0i2mjrvf2dgfrml.csv` or `five_firms_crsp_daily.csv`, each optionally
   followed by a numbered duplicate suffix before `.csv`.
3. `five_firms_permno_codes.txt` in the E: pilot folder. The producer reads
   exactly five unique decimal identifiers in its fixed AAPL, MSFT, AMZN,
   GOOGL, META order. This order is bound to the source code; the label file
   does not contain a PERMNO column.
4. Either of the two WRDS CIK-to-CUSIP and CRSP Names exports.
5. The other WRDS export. The checker assigns their roles from the headers,
   regardless of selection order.

The known download names are `foo5pewdxoicohvp.csv` and
`b19qnckqil9nsbjy.csv`; their roles are determined by their headers, not by
the random download stems. The launcher checks the E: local-check folder,
Downloads, and the E: drive root for these filenames. If the two roles are not
uniquely resolved,
it requests both files by dialog and classifies them by header.
If the original generated `five_firms_permno_codes.txt` is present beside the
WRDS checker, it must agree with the pilot copy.

The automatically located public `sec_metadata_inventory_v1.json` normally
resides in `artifacts/round8/`; the real round-4 `manifest.json` normally
resides in `artifacts/round4/20261031-small-chunk-real5-run-v1/`.

The pilot's `check_five_firms_wrds_daily.py` and
`make_five_firms_pilot_labels.py` are source code in the same E: folder. Their
exact bytes and imported paths must be checked before local execution; the
private CSV and text files are never read by a Codex session.
The separate `E:\five_firms_wrds_local_check\check_five_firms_wrds.py`
produces the ordered PERMNO codes. Its reviewed source SHA-256 is
`06c9717c44bec5ea8236612f2bc1595290db19b84dc5dd8f1ce7d825bad4a8a6`.
Its original launcher is `run_five_firms_wrds.cmd`; the join launcher uses the
pinned Python 3.11 environment and rechecks the source tables directly.

The tracked filing map, round-4 candidate, pinned model configuration, M5
proposal, metric contract and implementation code are resolved
from this checkout without dialogs. The SEC inventory is bound by its exact
published-metadata receipt hash. The five `ticker + SEC Filing Date` keys are
matched to `CIK + accession`, then to the round-4 Item 1 SHA-256. Filing Date
is not replaced by the SEC Accepted local date. The 128-body-token,
`equal_chunks` vector must have the saved filename and SHA-256, `float64`
dtype, `(384,)` shape and finite, nonzero values. NumPy pickle loading is
disabled. The round-4 manifest, sidecar, model revision, tokenizer and ONNX
file hashes, candidate and metric-contract bindings must match. No vector
values enter the output index.

## Actual pilot label and source fields

The pilot producer in the E: folder defines this exact label schema. The
earlier unadapted repository checker required different column names and a
bridge CSV; those are not inputs to this pilot. Other columns or renamed
fields must not be silently interpreted as equivalent.

| File | Required columns | Interpretation |
| --- | --- | --- |
| `five_firms_price_label_pilot.csv` | `ticker,filing_date,pilot_decision_session,fwd5_session,fwd21_session,price_return_5,price_return_21,scope,daily_sha256,permno_file_sha256` | Exactly one row per expected ticker; `scope` must be `ENGINEERING_ONLY`; dates are ISO `YYYY-MM-DD`; both price returns must be finite. The SHA-256 fields bind the selected daily CSV and ordered codes text bytes. |
| `five_firms_permno_codes.txt` | Five nonempty lines, each one unique 1–10 digit positive identifier | Positions correspond to the source code's fixed `FILINGS` order. The codes are checked against the two historical WRDS exports before a join can pass. |
| Daily CSV | `permno,dlycaldt,dlyclose,dlyprcflg,dlyfacprc,dlyret,dlyretx,dlyretmissflg,dlydelflg,dlydistretflg` | The producer requires these actual columns, checks each required session, finite positive close, no delisting flag, supported distribution flags, unit price factor and reconciliation of `dlyretx` with consecutive close ratios. This is the producer's bounded pilot rule, not a general CRSP adjustment contract. |
| WRDS CIK-to-CUSIP CSV | `cik,cusip,cusip_full,cikdate1,cikdate2,validated` | The reviewed checker accepts validation codes `2` or `3`, checks CUSIP consistency, and requires the first observed filing date to be no later than the relevant SEC Filing Date. `cikdate2` is the last observed filing date, not the effective end of a security link. |
| WRDS CRSP Names CSV | `permno,cusip,ticker,secinfostartdt,secinfoenddt` | The selected ticker and first eight CUSIP characters must match on the SEC Filing Date, which must fall in the inclusive CRSP Names interval. The result must be one unique PERMNO per filing and five distinct PERMNOs overall. |

[WRDS describes](https://wrds-www.wharton.upenn.edu/pages/wrds-research/database-linking-matrix/linking-sec-with-crsp/)
its CIK-CUSIP link as a historical bridge. The
[CRSP key-range description](https://www.crsp.org/wp-content/uploads/appendix/FlagType_FK.html)
identifies `PERMNO`, `SecInfoStartDt`, and `SecInfoEndDt` as a date-range key.
These public descriptions do not certify the particular local export bytes.

The label-producing code defines the price-return calculation as
`C(end) / C(decision) - 1` after its required action and daily-return checks.
The local join recomputes against the same source files under the hash-pinned
producer code and compares dates, numeric returns, scope and both source hashes
before accepting the saved labels. It does not print or store return values.
The ordered codes file and daily rows bind the engineering label to selected
identifier bytes. The previously missing WRDS security-checker source has now
been located and reviewed. The join repeats its CIK-to-CUSIP and effective
CRSP Names checks against the locally selected exports and requires the five
computed codes to equal the ordered codes file. It records both export hashes
and the checker source hash, then fails closed on any missing, ambiguous, or
changed source. A prior terminal `WRDS_FIVE_FIRMS_PASS` is supporting
information, not a substitute for this byte-bound local recheck. As of this
documentation update, Codex has neither opened the real exports nor run this
real-file check; its status remains `LOCAL_RUN_PENDING`.

This rule is a bounded engineering identity check. The selected ticker and
eight-character CUSIP match do not establish a general primary-share policy,
independent license review, or strict historical tradability. The WRDS daily
query success page alone does not establish the historical security link. No
boolean command-line override is available.

The pilot uses the Nasdaq session rule in the E: source code for December 2024
through December 2025, including the [January 9, 2025 temporary closure](https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2025-1). The
first listed session strictly after SEC Filing Date is `d`; `d+5` and `d+21`
are fixed positions in that pilot axis. The round-9 XNYS calendar is **not**
substituted. This is a bounded retrospective pilot calendar, not proof that
every issuer's historical listing MIC or each market-data observation is
certified. The index keeps `formal_m5_admission=null`.

After all five rows and the independent linkage source gate pass, an atomic
no-overwrite write creates
`artifacts/five_firms_text_label_pilot/five_firms_text_label_pilot_index.json`.
The `artifacts/` directory is Git-ignored; the script checks that fact before
writing. The index contains identities, source hashes, dates and relative
vector references, but no return values or vector contents. A different
existing index is never overwritten. Synthetic tests cannot authorize a
real-data pass.
