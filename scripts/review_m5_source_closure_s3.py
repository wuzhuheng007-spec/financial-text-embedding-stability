"""Review four existing SEC ambiguities and three frozen identity candidates.

This is local, source-bound preparation. It does not admit text or securities to
M5. Raw rows and excerpts are written only beneath ``local_only``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from finembed.extract import extract_item1, primary_text


ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT.parent / "financial-text-embedding-stability-publish"
PRIOR = ROOT / "artifacts" / "m5_next_20261114_0325"
DEFAULT_OUT = ROOT / "artifacts" / "m5_source_closure_20261114_0400"
EXPECTED_LEDGER = "eea1a6470e250438d30a0f3d63101e15d2ea973ef4b1999dbe85fcd5295320ea"
EXPECTED_SELECTION = "62843695e059344e7337a95668fc28f063e4441712e70b7e343c5d98ba93c737"
EXPECTED_INTERSECTION = "2374a22fb567676ab3df55e6da43c52328d393b1907be5704ac6637f8b8f4085"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def jread(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def jwrite(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def checked_file(path: Path, expected: str) -> None:
    actual = sha(path)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {path.name}: {actual}")


def sec_body(entry: dict) -> bytes:
    key = hashlib.sha256(entry["url"].encode("utf-8")).hexdigest()
    path = PRIOR / "sec_cache_private" / f"{key}.bin"
    checked_file(path, entry["raw_submission_sha256"])
    return path.read_bytes()


def filers(raw: bytes) -> list[dict]:
    header = raw.decode("utf-8", errors="replace").split("<DOCUMENT>", 1)[0]
    starts = list(re.finditer(r"^FILER:\s*$", header, re.I | re.M))
    result = []
    for i, start in enumerate(starts):
        block = header[start.start(): starts[i + 1].start() if i + 1 < len(starts) else len(header)]
        fields = {}
        for label, key in (
            ("COMPANY CONFORMED NAME", "name"), ("CENTRAL INDEX KEY", "cik"),
            ("FORM TYPE", "form"), ("SEC FILE NUMBER", "file_number"),
        ):
            match = re.search(r"^\s*" + re.escape(label) + r":\s*([^\r\n]+)", block, re.I | re.M)
            fields[key] = match.group(1).strip() if match else None
        result.append(fields)
    return result


def save_proposed_text(out: Path, accession: str, rendered: str, start: int, end: int) -> dict:
    if not (0 <= start < end <= len(rendered)):
        raise ValueError("provisional Item 1 offsets exceed the rendered source")
    text = rendered[start:end]
    if len(text.split()) < 150 or len(text) >= len(rendered) * 0.7:
        raise ValueError("provisional Item 1 span failed substantive or length check")
    path = out / "local_only" / "proposed_item1_text" / f"{accession}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() != data:
        raise FileExistsError(f"refusing to replace changed proposal {path.name}")
    path.write_bytes(data)
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            "start_char": start, "end_char_exclusive": end,
            "characters": len(text), "words": len(text.split()),
            "opening_excerpt": text[:260], "middle_excerpt": text[len(text)//2:len(text)//2+260],
            "ending_excerpt": text[-260:]}


def manual_boundary(rendered: str) -> tuple[str, int, int]:
    patterns = (
        ("combined_items_1_and_2", r"(?im)^Items 1 and 2\. Business and Properties\.$",
         r"(?im)^Item 1A\. Risk Factors\.$"),
        ("multiline_en_dash", r"(?im)^Item[ \t]*\n[ \t]*1[ \t]*\u2013[ \t]*Business$",
         r"(?im)^Item[ \t]*\n[ \t]*1A[ \t]*\u2013[ \t]*RISK FACTORS$"),
    )
    candidates = []
    for name, start_pattern, end_pattern in patterns:
        starts = list(re.finditer(start_pattern, rendered))
        ends = list(re.finditer(end_pattern, rendered))
        if len(starts) == 1 and len(ends) == 1 and starts[0].start() < ends[0].start():
            candidates.append((name, starts[0].start(), ends[0].start()))
    if len(candidates) != 1:
        raise ValueError(f"expected one reviewed alternate boundary, got {len(candidates)}")
    return candidates[0]


def csv_at_ordinals(path: Path, wanted: set[int]) -> dict[int, dict]:
    found = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for ordinal, row in enumerate(csv.DictReader(stream), 1):
            if ordinal in wanted:
                found[ordinal] = row
    if set(found) != wanted:
        raise ValueError(f"missing source row ordinal(s) in {path.name}: {wanted - set(found)}")
    return found


def csv_rows_for_ids(path: Path, field: str, ids: set[str]) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for ordinal, row in enumerate(csv.DictReader(stream), 1):
            if row[field] in ids:
                rows.append({"source_ordinal": ordinal, **row})
    return rows


def cover_class(rendered: str) -> dict:
    start = re.search(r"Securities registered pursuant to Section 12\(b\)", rendered, re.I)
    if not start:
        raise ValueError("primary document lacks Section 12(b) cover table")
    end = re.search(r"Securities registered pursuant to Section 12\(g\)", rendered[start.end():], re.I)
    if not end:
        raise ValueError("primary document lacks Section 12(g) cover boundary")
    excerpt = rendered[start.start(): start.end() + end.start()]
    lines = [line.strip() for line in excerpt.splitlines() if line.strip()]
    exchanges = [i for i, line in enumerate(lines) if line.casefold() == "new york stock exchange"]
    if len(exchanges) != 1 or exchanges[0] < 2:
        raise ValueError("cover table does not have one auditable NYSE security row")
    i = exchanges[0]
    return {"class": lines[i-2], "ticker": lines[i-1], "exchange": lines[i],
            "cover_start_char": start.start(), "cover_end_char_exclusive": start.end() + end.start(),
            "source_excerpt": excerpt[:800]}


def review_ambiguities(out: Path, attempts: list[dict]) -> tuple[dict, dict]:
    cases = [x for x in attempts if x["header_status"] == "ambiguous" or x["item1_status"] == "ambiguous"]
    if Counter((x["header_status"], x["item1_status"]) for x in cases) != {
        ("ambiguous", "not_checked"): 2, ("supported", "ambiguous"): 2,
    }:
        raise ValueError("the four frozen ambiguity categories changed")
    cases.sort(key=lambda x: (x["cik"], x["accession"]))
    private, safe = [], []
    for rank, entry in enumerate(cases, 1):
        raw = sec_body(entry)
        rendered, primary_locator = primary_text(raw)
        filing_blocks = filers(raw)
        if entry["header_status"] == "ambiguous":
            if len(filing_blocks) != 2 or len({x["cik"] for x in filing_blocks}) != 2:
                raise ValueError("joint filing is not two distinct SEC filers")
            text, locator = extract_item1(rendered)
            proposal = save_proposed_text(out, entry["accession"], rendered,
                                          locator["start_char"], locator["end_char_exclusive"])
            if text.encode("utf-8") != Path(proposal["path"]).read_bytes():
                raise ValueError("multi-filer proposal differs from source extraction")
            category = "two_distinct_filers_joint_report_policy_pending"
            heading = "base_item1_heading"
            substantive_note = "Item 1 discusses the trust and operating partnership together; the registered public class appears on the trust cover. Text remains entity-policy pending."
        else:
            if len(filing_blocks) != 1:
                raise ValueError("alternate heading case no longer has one filer")
            heading, start, end = manual_boundary(rendered)
            proposal = save_proposed_text(out, entry["accession"], rendered, start, end)
            category = "alternate_heading_source_exact_candidate_only"
            substantive_note = ("Body joins Business and Properties under Items 1 and 2; research text inclusion rule is needed."
                                if heading == "combined_items_1_and_2" else
                                "Body uses a multiline Item 1 en-dash heading; table-of-contents heading was excluded.")
        private.append({"anonymous_case": f"A{rank}", "accession": entry["accession"],
                        "source_url": entry["url"], "raw_submission_sha256": entry["raw_submission_sha256"],
                        "filers": filing_blocks, "primary_locator": primary_locator,
                        "heading_template": heading, "category": category,
                        "proposed_text": proposal, "substantive_review_note": substantive_note,
                        "original_ledger_status_preserved": True, "formal_text_admitted": False})
        safe.append({"anonymous_case": f"A{rank}", "category": category,
                     "filer_count": len(filing_blocks), "heading_template": heading,
                     "provisional_source_exact_text_saved_privately": True,
                     "formal_text_admitted": False})
    return ({"schema": "m5_s3_ambiguity_review_private_v1", "cases": private},
            {"schema": "m5_s3_ambiguity_review_safe_v1", "case_count": len(safe),
             "joint_two_filer_count": sum(x["filer_count"] == 2 for x in safe),
             "alternate_heading_count": sum(x["filer_count"] == 1 for x in safe),
             "cases": safe, "source_bodies_rehashed": len(safe), "formal_text_admitted": 0})


def review_identity(out: Path, selection: dict, ledger: dict, attempts: list[dict], intersections: dict) -> tuple[dict, dict]:
    screen_path = PUBLISH / "artifacts" / "m5_preparation_candidate_20261113_v1" / "m5_preparation_candidate_screen_private.json"
    source_registry_path = PUBLISH / "artifacts" / "m5_identity_candidate_ledger_20261113_v1" / "m5_identity_candidate_registry_private.json"
    ccm_registry_path = PUBLISH / "artifacts" / "m5_ccm_query11905330_intake_20261113_v1" / "m5_ccm_registry_private.json"
    source_registry = jread(source_registry_path)["source_manifest"]["registered_source_files"]
    ccm_bundle = jread(ccm_registry_path)["source_bundle"]
    checked_file(screen_path, "2254c697a3a0a3df0edac16b403a7737f0285f9f6735d06e5a37595bcd212602")
    screen = jread(screen_path)
    edges = {x["identity_candidate_id"]: x for x in screen["cik_observation_edge"]}
    sources = {
        "CIK_CUSIP": Path(source_registry["CIK_CUSIP"]["path"]),
        "SECURITY_STARTING": Path(source_registry["SECURITY_STARTING"]["path"]),
        "ISSUER_EARLY": Path(source_registry["ISSUER_EARLY"]["path"]),
        "CCM": Path(ccm_bundle["source_csv_path"]),
    }
    for role in ("CIK_CUSIP", "SECURITY_STARTING", "ISSUER_EARLY"):
        checked_file(sources[role], source_registry[role]["sha256"])
    checked_file(sources["CCM"], ccm_bundle["source_csv_sha256"])
    filing_rows = {x["accession"]: x for x in ledger["nondevelopment_candidate_rows"]}
    attempt_rows = {x["accession"]: x for x in attempts}
    intersection_rows = {x["accession"]: x for x in intersections["event_rows"]}
    selected = selection["selected_rows"]
    if len(selected) != 3:
        raise ValueError("expected three frozen selected identity reviews")
    source_ordinals, security_ordinals, ccm_ordinals = set(), set(), set()
    event_contexts = []
    for item in selected:
        row = filing_rows[item["accession"]]
        if row["cik"] != item["cik"] or row["item1_hash"] != item["item1_sha256"]:
            raise ValueError("selection differs from frozen filing row")
        ids = row["candidate_relation_source"]["identity_candidate_ids"]
        if len(ids) != 1 or ids[0] not in edges:
            raise ValueError("selected event has no unique saved candidate observation edge")
        edge = edges[ids[0]]
        if edge["cik"] != row["cik"] or edge["historical_valid_from"] is not None:
            raise ValueError("CIK observation edge changed status")
        source_ordinal = int(edge["source_record_id"].split(":")[-1])
        security_ordinal = int(edge["issue_evidence_id"].split(":")[-1])
        intr = intersection_rows[row["accession"]]
        if intr["matched_end_source_rows"] != 0 or intr["finite_source_rows_bracketing_filing"] != 1:
            raise ValueError("selected event is not the frozen finite-only intersection")
        ords = intr["matched_finite_end_source_ordinals"]
        if len(ords) != 1:
            raise ValueError("selected event needs one finite source row")
        source_ordinals.add(source_ordinal)
        security_ordinals.add(security_ordinal)
        ccm_ordinals.add(ords[0])
        event_contexts.append((item, row, edge, intr, source_ordinal, security_ordinal, ords[0]))
    cik_rows = csv_at_ordinals(sources["CIK_CUSIP"], source_ordinals)
    security_rows = csv_at_ordinals(sources["SECURITY_STARTING"], security_ordinals)
    ccm_rows = csv_at_ordinals(sources["CCM"], ccm_ordinals)
    permcos = {security_rows[x[5]]["permco"] for x in event_contexts}
    issuer_rows = csv_rows_for_ids(sources["ISSUER_EARLY"], "permco", permcos)
    private, safe = [], []
    for item, row, edge, intr, cik_ord, sec_ord, ccm_ord in event_contexts:
        attempt = attempt_rows[row["accession"]]
        raw = sec_body(attempt)
        rendered, primary_locator = primary_text(raw)
        filing_blocks = filers(raw)
        if len(filing_blocks) != 1 or filing_blocks[0]["cik"] != row["cik"]:
            raise ValueError("selected event has an unsupported SEC filer identity")
        item_locator = row["extraction_locator"]["item1"]
        item_text = rendered[item_locator["start_char"]:item_locator["end_char_exclusive"]]
        item_path = Path(row["extraction_locator"]["text_path_private"])
        if item_path.read_bytes() != item_text.encode("utf-8") or sha(item_path) != row["item1_hash"]:
            raise ValueError("Item 1 candidate does not match original rendered source")
        cover = cover_class(rendered)
        observation, security, ccm = cik_rows[cik_ord], security_rows[sec_ord], ccm_rows[ccm_ord]
        if observation["cik"] != row["cik"] or observation["cusip"] != security["cusip"]:
            raise ValueError("CIK/CUSIP observation does not match the security source")
        if ccm["cik"] != row["cik"]:
            raise ValueError("saved CCM joined Header CIK differs from the filing CIK")
        if security["permno"] not in row["candidate_security_ids"] or ccm["LPERMNO"] != security["permno"]:
            raise ValueError("candidate PERMNO mismatch")
        if ccm["cusip"] != security["cusip9"] or cover["ticker"] != security["ticker"] or ccm["tic"] != cover["ticker"]:
            raise ValueError("cover/security/CCM ticker or CUSIP mismatch")
        day = row["filing_date"]
        if not (observation["cikdate1"] < day < observation["cikdate2"]):
            raise ValueError("filing date not strictly interior to CIK/CUSIP observation range")
        if not (security["secinfostartdt"] < day < security["secinfoenddt"]
                and ccm["LINKDT"] < day < ccm["LINKENDDT"]):
            raise ValueError("filing date not strictly interior to saved intervals")
        issuer_on_day = [x for x in issuer_rows if x["permco"] == security["permco"]
                         and x["issinfostartdt"] < day < x["issinfoenddt"]]
        if len(issuer_on_day) != 1:
            raise ValueError("issuer source lacks one literal interval interior row")
        class_kind = "partnership_common_units" if "unit" in cover["class"].casefold() else "common_stock"
        private.append({
            "anonymous_event": f"E{item['selection_rank']}", "accession": row["accession"],
            "cik": row["cik"], "filing_date": day, "source_url": attempt["url"],
            "raw_submission_sha256": attempt["raw_submission_sha256"],
            "sec_filer": filing_blocks[0], "sec_cover": cover, "primary_locator": primary_locator,
            "item1_candidate": {"sha256": row["item1_hash"], "locator": item_locator,
                                "opening_excerpt": item_text[:260],
                                "middle_excerpt": item_text[len(item_text)//2:len(item_text)//2+260],
                                "ending_excerpt": item_text[-260:]},
            "cik_cusip_observation": {"source_ordinal": cik_ord, "source_row": observation,
                                         "source_sha256": source_registry["CIK_CUSIP"]["sha256"],
                                         "edge": edge},
            "dated_security_source": {"source_ordinal": sec_ord, "source_row": security,
                                      "source_sha256": source_registry["SECURITY_STARTING"]["sha256"]},
            "dated_issuer_source": {"source_row": issuer_on_day[0],
                                    "source_sha256": source_registry["ISSUER_EARLY"]["sha256"]},
            "ccm_finite_link_source": {"source_ordinal": ccm_ord, "source_row": ccm,
                                       "source_sha256": ccm_bundle["source_csv_sha256"]},
            "descriptive_class_kind": class_kind,
            "chain_assessment": {
                "filing_filer_and_sec_class": "SUPPORTED_IN_ORIGINAL_FILING",
                "cik_cusip": "CANDIDATE_OBSERVATION_DATES_NOT_VALID_TIME",
                "security_and_issuer_literal_interval": "SOURCE_ROWS_BRACKET_FILING_LITERAL_ENDPOINT_RULE_UNVERIFIED",
                "ccm_finite_link": "SOURCE_ROW_BRACKETS_FILING_BUT_HEADER_CIK_JOIN_TIME_UNKNOWN",
                "historical_issuer_to_security": "UNSUPPORTED_FOR_FORMAL_ADMISSION",
                "primary_security_selection": "UNSUPPORTED_NO_ACCEPTED_RESEARCH_RULE",
                "ordinary_share_eligibility": "UNRESOLVED_PARTNERSHIP_UNITS" if class_kind == "partnership_common_units" else "UNRESOLVED_SOURCE_RULE",
            },
            "minimal_missing": ["dated issuer-CIK/CUSIP/issue applicability and source semantics",
                                "saved CCM query Header join timing and release-bound link definition",
                                "accepted security class, market and primary-selection research rules"],
            "security_id": None, "formal_admitted": False,
        })
        safe.append({"anonymous_event": f"E{item['selection_rank']}",
                     "original_item1_candidate_source_exact": True,
                     "one_sec_filer": True, "literal_dated_security_row_interior": True,
                     "literal_finite_ccm_row_interior": True,
                     "cik_cusip_observation_only": True,
                     "descriptive_sec_class": class_kind,
                     "historical_issuer_to_security": "UNSUPPORTED_FOR_FORMAL_ADMISSION",
                     "primary_security_selection": "UNSUPPORTED_NO_ACCEPTED_RESEARCH_RULE",
                     "ordinary_share_eligibility": "UNRESOLVED",
                     "formal_admitted": False})
    binding = {"frozen_ledger_sha256": EXPECTED_LEDGER, "selection_sha256": EXPECTED_SELECTION,
               "intersection_private_sha256": EXPECTED_INTERSECTION,
               "screen_sha256": sha(screen_path),
               "cik_cusip_source_sha256": source_registry["CIK_CUSIP"]["sha256"],
               "security_starting_source_sha256": source_registry["SECURITY_STARTING"]["sha256"],
               "issuer_early_source_sha256": source_registry["ISSUER_EARLY"]["sha256"],
               "ccm_csv_sha256": ccm_bundle["source_csv_sha256"]}
    return ({"schema": "m5_s3_identity_chain_private_v1", "source_binding": binding, "events": private},
            {"schema": "m5_s3_identity_chain_safe_v1", "source_binding": binding,
             "selected_event_count": len(safe), "events": safe,
             "source_exact_item1_count": len(safe), "one_filer_count": len(safe),
             "literal_finite_ccm_interval_count": len(safe),
             "formal_historical_identity_supported_count": 0,
             "formal_primary_security_supported_count": 0,
             "formal_admitted_count": 0,
             "status": "SOURCE_FACTS_REVIEWED_IDENTITY_CHAIN_INCOMPLETE"})


def write_private_case_cards(out: Path, identity: dict) -> None:
    """One compact source trail per event, with licensed identifiers kept local."""
    sections = ["# S3 private event cards\n",
                "Source row details and complete SHA-256 bindings are also in `s3_identity_chain_private.json`. "
                "These cards are exploratory evidence reviews, not formal admissions.\n"]
    for event in identity["events"]:
        cover = event["sec_cover"]
        observation = event["cik_cusip_observation"]
        security = event["dated_security_source"]
        issuer = event["dated_issuer_source"]
        ccm = event["ccm_finite_link_source"]
        obs = observation["source_row"]
        sec = security["source_row"]
        iss = issuer["source_row"]
        link = ccm["source_row"]
        sections.append(f"## {event['anonymous_event']}: {event['accession']}\n")
        sections.append(f"Filing {event['filing_date']} · SEC CIK {event['cik']} · candidate PERMNO {sec['permno']} "
                        f"· `security_id=null` · formal admitted **false**.\n")
        sections.append("| Hop | Exact source evidence | Status |\n| --- | --- | --- |")
        sections.append(f"| SEC filer → security class | [Original submission]({event['source_url']}); body SHA-256 "
                        f"`{event['raw_submission_sha256']}`; header `{event['sec_filer']['name']}` / "
                        f"`{event['sec_filer']['cik']}`; cover `{cover['class']}` / `{cover['ticker']}` / "
                        f"`{cover['exchange']}`; Item 1 hash `{event['item1_candidate']['sha256']}`. | "
                        "Supported filing facts and source-exact candidate text only. |")
        sections.append(f"| CIK → CUSIP | CIK/CUSIP raw row {observation['source_ordinal']}, source SHA-256 "
                        f"`{observation['source_sha256']}`; CUSIP `{obs['cusip_full']}`; observation "
                        f"{obs['cikdate1']} to {obs['cikdate2']}. | Date span contains filing but is observation time, "
                        "not historical link validity. |")
        sections.append(f"| Issuer → dated security | Issuer row {iss['source_ordinal']} "
                        f"({iss['issinfostartdt']} to {iss['issinfoenddt']}); security row "
                        f"{security['source_ordinal']} ({sec['secinfostartdt']} to {sec['secinfoenddt']}), "
                        f"PERMCO `{sec['permco']}`, CUSIP `{sec['cusip9']}`, ticker `{sec['ticker']}`, "
                        f"class codes `{sec['securitytype']}/{sec['securitysubtype']}/{sec['sharetype']}`. | "
                        "Literal interval match; endpoint and class eligibility rules unaccepted. |")
        sections.append(f"| CCM finite row → candidate PERMNO | Query 11905330 row {ccm['source_ordinal']}; "
                        f"GVKEY `{link['GVKEY']}`, LIID `{link['LIID']}`, LINKDT `{link['LINKDT']}`, "
                        f"LINKENDDT `{link['LINKENDDT']}`, LINKTYPE `{link['LINKTYPE']}`, "
                        f"LINKPRIM `{link['LINKPRIM']}`, LPERMNO `{link['LPERMNO']}`. | Literal date bracket; "
                        "Header CIK join timing and release unknown. |")
        sections.append("\n**Decision:** Historical issuer-to-security `UNSUPPORTED`; primary security "
                        "`UNSUPPORTED`; formal event admission `false`. "
                        + ("Partnership-unit ordinary-share eligibility needs an explicit rule.\n"
                           if event["descriptive_class_kind"] == "partnership_common_units" else
                           "Common-stock description does not itself finish the formal identity rule.\n"))
        sections.append("**Minimal supplement:** dated CIK/CUSIP-to-issue applicability and interval definitions; "
                        "saved CCM Header join timing/release; accepted class and primary-security selection policy.\n")
    path = out / "local_only" / "s3_case_cards_private.md"
    content = "\n".join(sections)
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise FileExistsError("refusing to overwrite changed private case cards")
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = args.out
    checked_file(PRIOR / "candidate_events_private.json", EXPECTED_LEDGER)
    checked_file(PRIOR / "r3_candidate_intersection_private.json", EXPECTED_INTERSECTION)
    checked_file(out / "selected_identity_review_private.json", EXPECTED_SELECTION)
    selection = jread(out / "selected_identity_review_private.json")
    if selection["source_ledger_sha256"] != EXPECTED_LEDGER:
        raise ValueError("identity selection is not bound to frozen discovery ledger")
    ledger = jread(PRIOR / "candidate_events_private.json")
    intersections = jread(PRIOR / "r3_candidate_intersection_private.json")
    attempts = jread(PRIOR / "original_filing_download_log_private.json")["download_attempts"]
    private_ambiguity, safe_ambiguity = review_ambiguities(out, attempts)
    private_identity, safe_identity = review_identity(out, selection, ledger, attempts, intersections)
    write_private_case_cards(out, private_identity)
    jwrite(out / "local_only" / "s3_ambiguity_review_private.json", private_ambiguity)
    jwrite(out / "local_only" / "s3_identity_chain_private.json", private_identity)
    jwrite(out / "s3_ambiguity_cases_safe.json", safe_ambiguity)
    jwrite(out / "s3_identity_chain_safe.json", safe_identity)
    print(json.dumps({"status": "PASS_SOURCE_REVIEW", "ambiguous_cases": safe_ambiguity["case_count"],
                      "selected_identity_events": safe_identity["selected_event_count"],
                      "formal_admitted_count": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
