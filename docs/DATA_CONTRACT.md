# Data contract — SEC 10-K source

## Input and provenance

- The ordered demonstration list lives in `configs/smoke.yaml`. `first_run_symbol_limit: null` explicitly releases the M1 first-company limit. Each ticker is mapped through SEC official `company_tickers.json`, then `data.sec.gov/submissions/CIK##########.json` and referenced older submissions files are read. `corpus_index.json` records every inclusion, failure or blocking reason.
- The ticker map is fetched at run time and is **not** a historical ticker-to-CIK snapshot as of the smoke cutoff. Its potential future-identity leakage must be resolved before formal research.
- Choose the most recently accepted original `10-K` whose `acceptanceDateTime` local date is at or before `as_of`. `10-K/A` rows sharing its report date are recorded separately as amendments; they never silently replace the original.
- The live submissions API currently emits UTC `Z` acceptance timestamps. They are provisionally converted to `America/New_York` for discovery. In the observed five filings these times differ by 4–5 hours from their own raw `<SEC-HEADER>` acceptance values. Raw header accession, form and Eastern acceptance date are independently checked and determine the **final** as-of gate. Both timestamps and their difference are retained; a boundary-date discovery gap remains a formal-protocol risk. Naive older API values are interpreted as Eastern wall-clock time.
- Download the SEC complete submission text at `/Archives/edgar/data/{integer CIK}/{accession without dashes}/{accession}.txt`. Preserve bytes, URL, retrieval UTC time, acceptance time, filing date, report date, accession, content length and SHA-256.
- All SEC HTTP requests require `SEC_USER_AGENT` from a real process environment variable. The value is never copied to artifacts or logs. Cached bytes retain their original fetch timestamp and SHA-256.

## Extraction

- Select the SGML `<DOCUMENT>` with `<TYPE>10-K` from the complete submission; extract its `<TEXT>` and render HTML to plain text.
- Store the complete rendered primary document as `primary_text.txt`. Store Item 1 as an exact substring of that file with character offsets and SHA-256 in `item1_metadata.json`.
- Reject missing or ambiguous Item 1, a table-of-contents hit without substantive text, a missing next-section boundary, implausibly short sections, and competing long candidates. Failure is explicit; full text is never substituted as Item 1.
- `boundary_comparison.json` includes the start/end neighborhoods of the primary text and extracted text so the boundary can be checked by a person.

## Time and research boundary

`acceptanceDateTime` is the SEC acceptance timestamp; it is **not** proof of the instant a document became publicly available. As-of filtering is date-granular for this engineering smoke. No later study may treat it as a complete point-in-time availability guarantee. Research sampling, market decision times, availability lag, splits, price source and revision policy remain to be frozen in `PROTOCOL.md`.

M2 validation reparses every included raw submission and checks all raw/primary/Item 1 hashes, character offsets, SEC URL, acceptance cutoff and boundary comparison. Fewer than three valid real companies blocks similarity and stability outputs. The corpus audit names all exclusions.

## Verified primary references

- [SEC EDGAR API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces): submissions URL and older-file structure.
- [SEC developer FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions): ticker/CIK mapping, declared User-Agent, acceptance timestamps and availability lag.
- [SEC Accessing EDGAR Data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data): complete submission text archive path.
- [SEC Developer Resources](https://www.sec.gov/about/developer-resources): fair-access rate guidance.

