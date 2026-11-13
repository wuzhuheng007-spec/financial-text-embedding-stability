# Small-chunk engineering probe metric contract, version 1

**Status: PROPOSED; researcher acceptance not provided.** This contract describes
the fixed five-company M1 10-K Item 1 grid in
[`small_chunk_probe_round2.json`](../configs/small_chunk_probe_round2.json).
The executable numerical implementation is `finembed.small_chunk_metrics`.
Its pure function accepts smaller synthetic grids for software tests; the
real runner enforces this exact five-company, five-length candidate.
It produces descriptive measures, not a causal effect, semantic quality score,
significance test, danger threshold, financial loss estimate, or M5 protocol.
All measures below are retained; there is no post-run selection of favorable
arms. A formal study's primary metric and uncertainty design remain undecided.

## Grid, order, and validity

The ordered company IDs are AAPL, MSFT, AMZN, GOOGL, META. The full grid has
body lengths 118, 123, 128, 133, 138 and `equal_chunks` and
`body_token_weighted` pooling at each length. The reference for a length
comparison is **128 with the same pooling**. The reference for a pooling
comparison at fixed length is `equal_chunks` at that length. All five exact
source versions, one model/tokenizer, CPU provider, zero overlap, and the
predeclared normalization order must match. A changed length also changes
boundaries, chunk count and tail; the contrast cannot identify a pure token
count effect. This does not insert or remove document body text.

Each chunk vector is cast to float64 and L2-normalized, then pooled using
either `1/n_chunks` or `body_tokens_in_chunk / total_body_tokens`; specials
are excluded. The pooled company vector is L2-normalized. The pure metric
function again casts final company vectors to float64 and divides each row by
its norm before dot products. Nonfinite input, zero norm, mismatched dimension,
missing company or arm, and duplicate company ID fail the complete grid.
There is no zero-vector, subset, or missing-value imputation. The saved run
may preserve completed arms and failure reasons, but it must not emit a
five-company metric for an incomplete grid. `NULL` is allowed only for an
otherwise complete grid's undefined Spearman coefficient, with its reason.

Cosine matrices use raw float64 normalized dot products. The numerical code
does not round matrix scores or group near ties. For the cross-arm vector
distance only, the dot product is clamped to `[-1, 1]` before `1 - cosine`
to remove floating-point overshoot; this does not alter the ranking matrix.
Neither values nor ranks are rounded before comparison. Exact equal float64
scores use ascending company ID for peer order. Self is always excluded.
Spearman uses average ranks for exactly equal float64 upper-triangle values.

## Complete-grid descriptions

Let `u_i(L,p)` be the final unit company vector and
`S_ij(L,p) = u_i(L,p) dot u_j(L,p)`. Every matrix includes all five firms;
only `i<j` contributes to pair summaries. For each `L != 128` and pooling
`p`, report:

| Measure | Formula and output | Unit and denominator |
| --- | --- | --- |
| Company-vector cosine distance | `d_i(L,p)=1-clip(u_i(128,p) dot u_i(L,p), -1, 1)`; each firm and mean | Five firms; reference self-distance is zero by definition and is not a separate effect arm |
| Similarity change | Signed `DeltaS_ij(L,p)=S_ij(L,p)-S_ij(128,p)` and `abs(DeltaS_ij)` for every `i<j`; mean and maximum of absolute changes | Ten dependent company pairs; diagonal excluded |
| Upper-triangle Spearman | Pearson correlation of average ranks of the ten reference and candidate pair scores | Ten dependent pairs; `null` with reason when either rank vector is constant; never substitute zero |
| Top-2 overlap | `abs(N_i(128,p) intersection N_i(L,p))/2`; each firm and mean | Five firms; two peers per firm; exact score ties by ascending company ID |
| Rank-2/3 margin | For each firm, second peer identity, third peer identity, and `S_i,rank2-S_i,rank3`; reference/candidate gap and signed candidate-minus-reference gap | Five firms; an exact cutoff tie has gap zero and `exact_cutoff_tie=true` |
| Tracked peer movement | For each firm's reference rank-2 and rank-3 peers, their candidate rank and signed rank change; mean absolute rank shift | Ten tracked peer ranks, two per firm; all peers remain in the five-firm grid |

At **each** fixed length, also compare `body_token_weighted` against
`equal_chunks` using the same six measures, with the equal arm as reference.
This comparison reuses the same chunk vectors from that company and length.
The report includes the full ten-cell similarity matrix and four ordered peers
for every arm, including the 128 reference arms. The 128 self-comparison is
omitted from the length-change table because its change is identically zero.

For each `L != 128` and `i<j`, retain the signed descriptive interaction
`I_ij(L) = DeltaS_ij(L,body_token_weighted) - DeltaS_ij(L,equal_chunks)`.
Report all ten values and their **mean signed interaction** over ten dependent
pairs. Do not subtract absolute changes or call this a causal interaction,
stabilization benefit, or population estimate. No significance result follows
from the ten pairs because they share firms.

## Reporting and failure policy

The output reports every predeclared length, pooling arm, company, pair and
metric. No numerical danger cutoff or significance claim is set. A failed
encoding job is retained with its reason; a partial run status is distinct
from a complete grid and has no full-grid metrics. Recalculation from saved
chunk and company vectors must reproduce each pooling output and metric file.
Any changed source text, model/tokenizer, grid, pooling meaning, or metric
contract requires a new researcher decision. A changed executable source or
runtime dependency without a scientific definition change requires a new
tokenizer-only preflight and explicit software review before future execution.
