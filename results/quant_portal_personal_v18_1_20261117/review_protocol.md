# v1.8.1 development review protocol

Frozen before new generation. All 16 READY event segments from the v1.8
private selection are reused in their original order. No item is replaced on
the basis of model output. The same Qwen2.5-3B revision, seed, input cap,
generation cap and timeouts are retained. The changed prompt and changed
contract form one engineering combination; their separate causal effects
cannot be estimated here. No 7B candidate or new source download is planned.

Raw JSON parsing, schema/ID/exact-phrase checks, and source-semantic review are
reported separately. A source actor may be any exact source phrase, including
agencies or people outside the watchlist. Deterministic watchlist mentions
mean only that an alias appeared, not that the entity acted or was affected.
For actor, attribution, claim kind, category, tone and uncertainty, review
whether the selected source sentence supports that particular field. Source
localization does not verify whether the supplier's report is true.

Review labels: CORRECT, WRONG_ACTOR, WRONG_ATTRIBUTION, WRONG_CLAIM_STATUS,
WRONG_CATEGORY, WRONG_TONE, WRONG_UNCERTAINTY, UNSUPPORTED_EVIDENCE,
MISSING_FIELD, FORMAT_ERROR, ABSTAINED, UNRESOLVED. Multiple labels may
apply. Full content acceptance requires a mechanically valid, non-abstaining
response with all asserted fields supported and no semantic error. Unknown
fields are permitted but reduce field coverage; an all-unknown response
cannot count as useful extraction.

The reviewer is Codex on already-seen development material. This is neither
independent human gold nor an estimate of general extraction accuracy.
All raw source and response text stays in the private artifact directory.
