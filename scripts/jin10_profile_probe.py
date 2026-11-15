"""Derive a redacted structural profile from one ignored local MCP probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.jin10_mcp.engine import source_time, unwrap_mcp_payload


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "artifacts"
PUBLIC_ROOT = ROOT / "results/news_jin10_20261115"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_captures(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Captured file must be an array")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--public-output", type=Path, required=True)
    args = parser.parse_args()
    private = args.private_dir.resolve()
    public = args.public_output.resolve()
    if not private.is_relative_to(PRIVATE_ROOT) or not public.is_relative_to(PUBLIC_ROOT):
        raise ValueError("Private input and public redacted output roots are fixed")
    if public.exists():
        raise FileExistsError("Preserve prior profile")
    flash_path = private / "flash_captures.json"
    calendar_path = private / "calendar_captures.json"
    tools_path = private / "tools_list.json"
    flashes = read_captures(flash_path)
    calendars = read_captures(calendar_path)
    flash_items = []
    for capture in flashes:
        data, _ = unwrap_mcp_payload(capture["response"])
        if data.get("status") != 200 or not isinstance(data.get("data", {}).get("items"), list):
            raise ValueError("Flash response was not a successful item page")
        flash_items.extend(data["data"]["items"])
    calendar_items = []
    for capture in calendars:
        data, _ = unwrap_mcp_payload(capture["response"])
        if data.get("status") != 200 or not isinstance(data.get("data"), list):
            raise ValueError("Calendar response was not a successful array")
        calendar_items.extend(data["data"])
    flash_urls = [str(row.get("url", "")) for row in flash_items]
    url_shapes = Counter()
    for value in flash_urls:
        parsed = urlsplit(value)
        path_prefix = "/" + parsed.path.strip("/").split("/", 1)[0] if parsed.path else ""
        url_shapes[f"{parsed.scheme}://{parsed.hostname or ''}{path_prefix}/..."] += 1
    flash_time = Counter(source_time(row.get("time"), None)[2] for row in flash_items)
    calendar_time = Counter(source_time(row.get("pub_time"), None)[2] for row in calendar_items)
    calendar_missing = {field: {
        "null": sum(row.get(field) is None for row in calendar_items),
        "empty_string": sum(row.get(field) == "" for row in calendar_items),
    } for field in ("actual", "consensus", "previous", "revised")}
    profile = {
        "schema": "jin10_probe_structural_profile_v1",
        "input_private_paths": {
            "tools_list": tools_path.relative_to(ROOT).as_posix(),
            "flash_captures": flash_path.relative_to(ROOT).as_posix(),
            "calendar_captures": calendar_path.relative_to(ROOT).as_posix(),
        },
        "input_sha256": {"tools_list": sha256(tools_path),
                         "flash_captures": sha256(flash_path),
                         "calendar_captures": sha256(calendar_path)},
        "flash": {
            "pages": len(flashes), "items_scanned": len(flash_items),
            "independent_id_field_present": sum("id" in row for row in flash_items),
            "nonempty_content": sum(bool(str(row.get("content") or "").strip()) for row in flash_items),
            "nonempty_title": sum(bool(str(row.get("title") or "").strip()) for row in flash_items),
            "nonempty_url": sum(bool(value.strip()) for value in flash_urls),
            "unique_url_count": len(set(flash_urls)),
            "url_shape_counts": dict(sorted(url_shapes.items())),
            "time_precision_counts": dict(sorted(flash_time.items())),
        },
        "calendar": {
            "pages": len(calendars), "items_scanned": len(calendar_items),
            "time_field_semantics": "pub_time is provider's theoretical scheduled publication time",
            "pub_time_precision_counts": dict(sorted(calendar_time.items())),
            "missing_values": calendar_missing,
            "duplicate_title_pub_time_count": len(calendar_items) - len({
                (row.get("title"), row.get("pub_time")) for row in calendar_items}),
        },
        "scope": "Only captured pages; no whole-service coverage inference",
        "content_or_row_values_published": False,
    }
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    print(json.dumps({"profile": public.relative_to(ROOT).as_posix(),
                      "flash_items_scanned": len(flash_items),
                      "calendar_items_scanned": len(calendar_items)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
