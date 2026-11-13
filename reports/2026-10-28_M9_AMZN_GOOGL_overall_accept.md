# M9 AMZN and GOOGL overall researcher decision addendum

**Scope.** One later user reply explicitly supplied overall `ACCEPT`, a reviewer name, and a Pacific local review time in direct response to a request for the two companies' separate overall choices, name, and actual review time. The exact name and original wording are retained in ignored private sidecars rather than copied into this tracked report. The same reply applies to AMZN and GOOGL; it is one grouped message, not two independent reviews or a new set of thirty item-specific comments. Earlier 12/12 AMZN and 18/18 GOOGL item choices remain unchanged.

## Plan versus actual

| Step | Actual result | Limit |
| --- | --- | --- |
| Recheck state | Baseline commit `87c82729d26cf40c56cae66cb879577bb91bfc3e` and clean worktree were confirmed before this addendum. Earlier item-decision files and source versions were rehashed. | The earlier report's `NOT_MET` gates remain a valid pre-overall-decision snapshot. |
| Bind AMZN overall reply | [AMZN overall event](../artifacts/m9/20261027T232114Z-amzn-overall-accept-v1/amzn_overall_decision_v1_en.json) records `ACCEPT`, the entered name/time, the previous 12/12 choices, and exact raw, old Item 2, proposed structured text, sheet, and comparison hashes. | Existing v4 form binds the old text and was not exported or rewritten to approve the derivative. |
| Bind GOOGL overall reply | [GOOGL overall event](../artifacts/m9/20261027T232123Z-googl-overall-accept-v1/overall_accept_event_v2_en.json) and [state](../artifacts/m9/20261027T232123Z-googl-overall-accept-v1/review_state_v2_en.json) record `ACCEPT`, the entered name/time, the previous 18/18 choices, and exact old/source/proposed text, page, comparison, and memo hashes. | MDA-1/MDA-5 source header-to-value mappings remain `UNRESOLVED`; generated labels are not SEC wording. |
| Evaluate gates | Both exact proposed derivatives meet the M9 **form-field-equivalent** `HUMAN_DECISION_RECORDED` state and `ELIGIBLE_FOR_SEPARATE_PROJECT_VALIDATION` gate: program boundary pass, all items `ACCEPT`, nonblank reviewer and review time, and overall `ACCEPT`. | The separate project validation has **not run**. These are version-bound sidecars, not exported records from old v4 forms; neither is a final accepted multi-company corpus. |
| Verify and close locally | The AMZN sidecar readback passed 41/41 checks; GOOGL's sidecar manifest and readback passed. The [cross-company readback](../artifacts/m9/20261027T232247Z-overall-closeout/overall_cross_company_readback.json) rechecked 15 AMZN and 19 GOOGL manifest entries, the matching original reply, time conversion, field gates, and three report links. | These are same-team self-checks. New comparison-page browser rendering remains `NOT_VERIFIED`; no model, finance, or remote publication step was performed. |

## Time and decision provenance

The exact original user wording is preserved in private local sidecars, separate from this English interpretation. The user did **not** type a year. The local time was interpreted in `America/Los_Angeles` using the current October 2026 project context and local `tzdata 2026.2`; the normalized instant and the year-inference flag are in each version-bound event. The agent's record-creation timestamp is distinct from the user's entered review time. No separate handwritten or cryptographic signature was supplied or required by the M9 form field rule.

The AMZN event SHA-256 is `36d3add2909ae5024801f2c63d8188526c1db581e950ed60668ed62dfffe6237`. It chains the prior AMZN item record SHA-256 `835765363d6a4db5d321b3bc4ebf5d86d47e5578e451d94f636a6b7bfd37f987` and proposed text SHA-256 `2d9a0eed0b191decee43a6fea9e23ce6af601ce34a44e8eb9aa7a622898bc6d7`.

The GOOGL overall event SHA-256 is `44b87d1dca5f1484b5915fbecd9d216e011c324fdd07e5294f027333cdfeff22`; its new state SHA-256 is `fcece218ca61bd6c378c8c0259bd2a64e5b0e42fe034c829d963d521ad8e45d0`. It chains prior 18/18 state SHA-256 `9b6c5211b289b02eb714106a033184f685dfa66d6f9f4cc5973c2d562834b51d` and proposed text SHA-256 `8d9eb5244c1293e77260a0d8c4031c0d604f1b7294fa88827cb30c63f8c6dc70`.

## Remaining project limits

The company-level overall choices are now actually recorded for the exact proposed fixed texts. They do not prove the GOOGL sparse table semantics, browser display, first-public or tradable availability, normalized absolute dates for relative phrases, or financial incremental value. Formal M5 parameters remain unfrozen. No five-company 10-Q inference, return prediction, backtest, or risk optimization follows from this decision alone.

The next minimum engineering step is a **separate, version-aware project validation** of the two candidate inputs and their remaining exceptions. It should report GOOGL MDA-1/MDA-5, new-page display evidence, and any effect on eligibility explicitly before assembling a multi-company descriptive corpus. It must not relabel this same-team readback as an independent audit.

The final readback command was:

```powershell
python -X utf8 artifacts/m9/20261027T232247Z-overall-closeout/verify_overall_handoff.py
```
