# Open-source proposal and release boundary

Checked 2026-11-16 (Asia/Shanghai). This is a **proposal**, not an applied license. The repository currently has no root `LICENSE`; [SHARING_SCOPE.md](../../SHARING_SCOPE.md) explicitly says no license is implied. Git history has multiple author spellings for the same displayed account but contribution ownership and third-party rights still require review. Do not add a root license or reinterpret older research code as Apache-2.0 merely by adding this file.

## Proposed scope

If owners and contributors approve, the new `apps/quant_portal` code and its original documentation could be released under [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt). The final release would add the unmodified license text, correct copyright/NOTICE information, and file headers where appropriate. The [Apache licensing FAQ](https://www.apache.org/foundation/license-faq) describes inclusion of the license and NOTICE. Until that decision, mark licensing `PROPOSED / NOT_GRANTED`.

Excluded from any code release: private research databases, original WRDS/market/news rows, provider text, OAuth/API credentials, model weights, user accounts, private Site source state, and any third-party asset without its own permission. The app itself has no market-data redistribution license.

## Required checks before changing license state

1. Confirm ownership and contributor consent for the exact files being relicensed; identify any copied code, HTML/CSS, icons or fonts from third parties.
2. Freeze direct and transitive dependency versions, record their licenses from package metadata and official project sources, and check NOTICE obligations. A dependency list is not an ownership transfer.
3. Scan intended current publication and reachable Git history for credential patterns, private paths and restricted content; report only locations and pattern names, never secret values. Rotate/revoke any real exposed credential through its provider before broader publication. Do not rewrite the research repository history in this task.
4. Obtain provider-specific written rights before exposing moomoo/Jin10 data to invited users; a private or free app still has multiple recipients.
5. Review the exact final Git diff and artifact ZIP for account data, private database files, raw news, historical prices and unpublished source excerpts.

## Third-party notice status

The [pinned dependency inventory](third_party_inventory.md) now records installed Python package metadata and versions. Full license texts, native/container layer notices and a deployable image SBOM remain pending because the local container engine is unavailable. The new interface reuses visual direction from the retained private Site; its templates and CSS were authored in this round without copying third-party icons, images, fonts or JavaScript. No upstream data or model is bundled. See [source rights](source_rights_register.md) for the independent data-permission gate.
