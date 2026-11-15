"""Static privacy and content checks for the new Jin10 dashboard variants."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.jin10_mcp.engine import unwrap_mcp_payload
from scripts.jin10_mcp.export import export_display


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "artifacts"
PUBLIC_ROOT = ROOT / "results/news_jin10_20261115"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-db", type=Path, required=True)
    parser.add_argument("--private-captures", type=Path, required=True)
    parser.add_argument("--private-html", type=Path, required=True)
    parser.add_argument("--public-html", type=Path, required=True)
    parser.add_argument("--public-receipt", type=Path, required=True)
    args = parser.parse_args()
    db, captures, private_html = (item.resolve() for item in
                                   (args.private_db, args.private_captures, args.private_html))
    public_html, public_receipt = args.public_html.resolve(), args.public_receipt.resolve()
    if not all(path.is_relative_to(PRIVATE_ROOT) for path in (db, captures, private_html)):
        raise ValueError("Provider data paths must remain ignored private artifacts")
    if not all(path.is_relative_to(PUBLIC_ROOT) for path in (public_html, public_receipt)):
        raise ValueError("Public artifacts must remain in Jin10 results")
    if public_receipt.exists():
        raise FileExistsError(public_receipt)
    private_page = private_html.read_text(encoding="utf-8")
    public_page = public_html.read_text(encoding="utf-8")
    if "PUBLIC_TEMPLATE_PRIVATE_FEED_OMITTED" not in public_page:
        raise AssertionError("Public template does not mark private feed omission")
    if "LOCAL_CAPTURE_WITH_COVERAGE_GAP" not in private_page:
        raise AssertionError("Private page omitted partial flash status")
    if "Scheduled:" not in private_page or "provider theoretical time" not in private_page:
        raise AssertionError("Private page omitted calendar time semantics")
    flash_captures = json.loads(captures.read_text(encoding="utf-8"))
    provider_bodies = [item.get("content") for capture in flash_captures
                       for item in unwrap_mcp_payload(capture["response"])[0]["data"]["items"]]
    body_leaks = sum(bool(body and len(body) >= 20 and body in public_page)
                     for body in provider_bodies)
    if body_leaks:
        raise AssertionError("Public template contains captured provider text")
    exported = export_display(db, limit=50, include_private_content=False)["records"]
    flash = [row for row in exported if row["tool_name"] == "list_flash"]
    calendar = [row for row in exported if row["tool_name"] == "list_calendar"]
    related = [row for row in flash if row["issuer_candidates"] or
               row["event_category_candidate"] != "UNCLASSIFIED"]
    if f"Related Jin10 flash candidates · {len(related)} of {len(flash)}" not in private_page:
        raise AssertionError("Flash display counts do not match export")
    if f"Jin10 calendar source reports · {len(calendar)}" not in private_page:
        raise AssertionError("Calendar display count does not match export")
    receipt = {
        "schema": "jin10_dashboard_static_validation_v1",
        "public_template_sha256": sha256(public_html),
        "private_local_html_sha256": sha256(private_html),
        "private_flash_capture_sha256": sha256(captures),
        "flash_rows_in_display_export": len(flash),
        "related_flash_keyword_candidates_shown": len(related),
        "calendar_rows_in_display_export": len(calendar),
        "provider_flash_bodies_checked_for_public_leak": len(provider_bodies),
        "provider_flash_body_leaks_into_public_template": body_leaks,
        "private_html_published": False,
        "browser_visual_acceptance": "NOT_RUN",
        "static_checks": "PASS",
    }
    public_receipt.parent.mkdir(parents=True, exist_ok=True)
    public_receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8", newline="\n")
    print(json.dumps({"static_checks": "PASS", "related_flash_shown": len(related),
                      "calendar_shown": len(calendar), "browser_visual_acceptance": "NOT_RUN"}))


if __name__ == "__main__":
    main()
