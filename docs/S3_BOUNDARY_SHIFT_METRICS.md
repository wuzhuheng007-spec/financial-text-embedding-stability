# S3 boundary-shift engineering metric contract, version 1

**Scope:** The researcher's round 6 acceptance covers the previously displayed
S3 mathematical scheme. This file formalizes its implementation after that
message; its later hash does not imply the researcher reviewed these bytes in
advance. The candidate remains the unchanged
[`boundary_shift_probe_round5.json`](../configs/boundary_shift_probe_round5.json)
with historical `PROPOSED / NOT_RUN` fields. A separate private decision record
captures the acceptance and its exact execution bindings. The existing
[`SMALL_CHUNK_METRICS.md`](SMALL_CHUNK_METRICS.md), SHA-256
`58a7b2f1389be691ef979f4fc4710f11d29027617c93f5a4eab580f73f3a6402`,
remains byte unchanged and supplies the numerical semantics reused here.

## Axis, grid and normalization

The experimental axis is **boundary shift** `delta`, not body chunk length.
The base step is always 128 complete body token IDs. For each of the exact five
M1 Item 1 texts, `n=ceil(T/128)` chunks cover all `T` body IDs with endpoints
`0,T` and internal cuts `128*j+delta`, `j=1,...,n-1`. The declared deltas are
`-10,-5,0,5,10`; delta `0` is the within-pooling reference. The first and tail
body lengths change with delta, while complete interior lengths remain 128.
No clipping, retokenization, overlap, reordering, missing firm or text
substitution is allowed. The five firm IDs and order are AAPL, MSFT, AMZN,
GOOGL, META. Each delta has `equal_chunks` and `body_token_weighted` arms.

One chunk-vector set is encoded per company and delta, and both pooling arms
reuse it. Each chunk vector is cast to float64 and L2 normalized. Equal
pooling assigns `1/n`; weighted pooling assigns actual body IDs in the chunk
divided by `T`, excluding specials. The pooled company vector is L2 normalized.
For numerical metrics, final company vectors are again cast to float64 and
row-normalized before dot products. This preserves the original contract's
normalization and pooling order. No result is reported for an incomplete grid.

## Complete descriptive output

Let `u_i(delta,p)` be the final unit vector for company `i` under pooling `p`,
and `S_ij(delta,p)=u_i(delta,p) dot u_j(delta,p)`. Each arm retains the full
five-by-five similarity matrix, four ordered peers per firm, Top-2 peer set,
rank-2 and rank-3 identities, their score gap and exact-cutoff-tie flag.

For **each nonzero delta and same pooling**, compare with `(0,p)`:

| Measure | Formula or computation | Unit and denominator |
| --- | --- | --- |
| Company-vector cosine distance | `1-clip(u_i(0,p) dot u_i(delta,p),-1,1)` | Every firm and mean over five firms |
| Similarity change | Signed `D_ij=S_ij(delta,p)-S_ij(0,p)` and `abs(D_ij)`; retain every value and mean/maximum absolute change | Ten dependent `i<j` pairs; exclude diagonal |
| Upper-triangle Spearman | Pearson correlation of average ranks of the two ten-pair score lists | Ten dependent pairs; `null` plus reason if either rank list is constant |
| Top-2 overlap | `size(N_i(0,p) intersection N_i(delta,p))/2` | Each firm and mean over five firms |
| Rank-2/3 margin | Retain second and third peer IDs and their score difference for each arm; report candidate-minus-reference gap | Five firms |
| Tracked peer movement | Track the reference rank-2 and rank-3 peer IDs in the candidate order; report their ranks, signed change and mean absolute change | Ten firm-peer ranks, two per firm |

At **each fixed delta**, compare `body_token_weighted` against
`equal_chunks` with equal as reference using those same six metric families.
Include the delta-zero pooling comparison. Omit only the identically zero
within-pooling delta-zero self-comparison; retain both delta-zero arm
descriptions. The executable pure function is
`finembed.boundary_shift_metrics.compute_boundary_shift_metrics`, which
delegates all floating-point, ranking and comparison arithmetic to
`finembed.small_chunk_metrics` and labels every output arm by `boundary_shift`.

For every nonzero delta and `i<j`, preserve the **signed** interaction:

`I_ij(delta) = [S_ij(delta,weighted)-S_ij(0,weighted)] - [S_ij(delta,equal)-S_ij(0,equal)]`.

Retain the ten pairwise values and their mean signed value over ten dependent
pairs. This is a descriptive difference of contrasts, not a causal effect.
Do not subtract absolute changes or call the interaction a stabilization gain.

## Ties, invalid values and reporting

Matrix scores are raw float64 normalized dot products and are never rounded
before comparison. Only the cross-arm vector-distance dot product is clipped
to `[-1,1]` for numerical overshoot. Peers are ordered by descending exact
float64 cosine, breaking an exact tie by ascending company ID; self is
excluded. Spearman gives exactly tied float64 pair scores average ranks, with
no near-tie tolerance. A constant rank vector makes Spearman undefined and
is emitted as `null` with a reason, never zero. An exact rank-2/3 score tie
has gap zero and an explicit flag.

Missing arms or firms, extra arms, duplicate company IDs, inconsistent shape
or dimension, nonfinite values and zero norms reject the complete metrics.
No zero-vector, subset or missing-value imputation is allowed. A failed task
retains its reason and partial outputs, but a partial grid has no five-company
metrics file. Report **all ten delta-by-pooling settings**, all eight nonzero
same-pooling shift comparisons, all five same-delta pooling comparisons, and
all four interaction sets. There is no post-run favorable-arm selection,
significance test, population estimate, danger threshold or financial claim.
The ten company pairs share firms and are not independent observations.

S3 is exploratory after round 4. It holds complete text and chunk count but
changes chunk content, first/tail lengths and weighted-pooling weights. A
future observation therefore cannot isolate a pure semantic-boundary cause.
The separate version-bound S3 decision and a passing current tokenizer-only
receipt are execution gates; this document does not itself authorize model
loading.
