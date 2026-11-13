# M5 CIK-CUSIP dictionary screenshot evidence

**2026-11-13; ENGINEERING_ONLY metadata update.** The [machine-readable field record](../configs/m5_cik_cusip_dictionary_evidence_20261113.json) transcribes all 11 variable names, displayed types and exact labels from two user-supplied WRDS Variable Descriptions screenshots. The original six screenshots remain in a Git-ignored local receipt. The input handoff ZIP SHA-256 is `ac0e26fbafb88f9c597fcdeb87de1eab6507dfc3c46f2a571049f27083008d51`; the locally verified screenshot/field receipt SHA-256 is `95631849d215c312376a743a9100e4b852b118dfdb4de3525a59cf24995f14dd`. These hashes bind files, not a live authenticated page session or an earlier export snapshot.

The page displays **Product** `wrdssec_common`, **Library** `wrdssec`, **File** `wciklink_cusip`, **Last Updated On** `2026-11-11` and **Update Frequency** `Daily`. These are separate display labels. The product name is not inferred to be an executable SQL schema, and the displayed update date is not the release identifier, timezone, download date, license or exact version of the previously saved link CSV. The six filenames do not establish their precise capture UTC.

| Field or category | Screenshot-supported label or interpretation | Limit |
| --- | --- | --- |
| `cikdate1` | First Filing Date of CIK-CUSIP Combination | First observed filing date, not a CIK-to-security effective start. |
| `cikdate2` | Last Filing Date of CIK-CUSIP Combination | Last observed filing date, not an effective end or a public-time timestamp. |
| `validated=0` | Invalid CUSIP | Provider category, retained as source evidence. |
| `validated=1` | Name Mismatch | Provider category, not an automatic exclusion rule. |
| `validated=2` | Valid 8digit CUSIP Link | Provider category, not researcher acceptance. |
| `validated=3` | Valid 9digit CUSIP Link | Provider category, not a preaccepted priority over code 2. |

The screenshot's `issue_check` label reads **CUSIP Issuer Check Digit** and is preserved verbatim in the sidecar. `cik` is labeled SEC CIK Number; the page does not resolve the exact Schedule 13D/G subject-role parsing. CIK/CUSIP identifiers remain strings, and `tmatch` gains no project weight or threshold from its displayed Float type.

The current Query Form screenshot checks the two matching-name categories and leaves name mismatch and invalid link unchecked. This records the **currently visible form state only**; it does not establish the WHERE predicate, complete code coverage or exact version of the earlier saved export. Existing local source registration binds that export's file identity, but no historical parameter record for this specific filter was verified here.

The [identity candidate ledger](M5_IDENTITY_CANDIDATE_LEDGER.md) already stores these first/last filing dates as `OBSERVATION_TIME` and keeps CIK valid-from/to, primary security, MIC, eligibility and accepted candidate status unknown or false. No old ledger or WRDS CSV was rebuilt for this definition update. A filing observation span does not prove every intervening day was a valid CIK-to-issue relationship; neither endpoint inclusivity nor first-public availability is established. The [readiness matrix](M5_DATA_ADMISSION_READINESS_20261112.md) and [updated minimum request](M5_IDENTITY_DEFINITION_REQUEST_20261113.md) distinguish the now-supported basic labels from remaining historical-identity evidence.

The [WRDS public linking description](https://wrds-www.wharton.upenn.edu/pages/wrds-research/database-linking-matrix/linking-sec-with-crsp/) supports the Schedule 13D/G-derived purpose, but not these exact field definitions; those are supported by the user-supplied screenshots. The query and data-dictionary entries redirected to login during this review, and no WRDS login or query was attempted. Scope A is the previously recorded retrospective fixed-byte direction; the unchanged round-8 M5 candidate remains `PROPOSED / researcher_accepted=false / NOT_RUN`. Metadata receipt does not admit a formal cohort or financial observation.
