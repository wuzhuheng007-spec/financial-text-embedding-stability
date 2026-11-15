# Financial text v2.1 input correction

Scope: the same eight saved NVDA/MU earnings exhibits and eight accession-matched
SEC primary filings dated 2025-04-01 through 2026-03-31. The original v2 input,
fact review, and first P4 retention receipts remain frozen. The event adapter
budget stays at 20,000 characters; no model is fitted.

Training-source inspection found two table-header failures in v2. Micron's
`FQ3-25` labels were not recognized as periods. NVIDIA's cash-flow date rows
followed the first four table rows and were not inherited. The formatted v2
table rows also omitted a contiguous copy of the source row, preventing an
exact evidence-fragment check. v2.1 recognizes explicit `FQn-yy` labels,
collects period/unit/basis header rows until the first topic-bearing numeric
data row, and carries the exact source header rows and source data row into
each row's actual adapter text along with the column mapping.

This is a deterministic input repair on reused training material. The rule
designer had already seen earlier validation results; no validation or test
bodies or labels are reopened for this correction. Table hints remain
candidate context until checked against the source. A longer row can still be
excluded by the fixed adapter budget, and unknown source fields stay unknown.
