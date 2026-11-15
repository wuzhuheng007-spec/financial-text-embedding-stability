"""Bind local intake shapes to real tools/list and the saved bounded probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.jin10_mcp.cli import load_spec, tool_from_listing
from scripts.jin10_mcp.engine import checked_source_url, parse_page, unwrap_mcp_payload
from scripts.jin10_mcp.schema import ToolSpec
from scripts.jin10_mcp.store import canonical


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "artifacts"
PUBLIC_ROOT = ROOT / "results/news_jin10_20261115"


def sha_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_object(value: dict) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def make_shape(tool: dict, kind: str, response: dict, capture_file: str) -> dict:
    input_schema = tool["inputSchema"]
    if input_schema.get("type") != "object" or input_schema.get("required"):
        raise ValueError("Live input schema changed from no-required-argument probe")
    if kind == "FLASH":
        properties = input_schema.get("properties", {})
        if properties.get("cursor", {}).get("type") != "string":
            raise ValueError("Live flash cursor parameter differs from probe")
        output = tool.get("outputSchema", {}).get("properties", {}).get("data", {})
        output_properties = output.get("properties", {})
        if not {"items", "has_more", "next_cursor"}.issubset(output_properties):
            raise ValueError("Live flash output schema differs from observed page")
        item_properties = output_properties["items"].get("items", {}).get("properties", {})
        if not {"url", "time", "content"}.issubset(item_properties):
            raise ValueError("Live flash item fields differ from observed page")
        if "原文链接" not in item_properties["url"].get("description", ""):
            raise ValueError("Provider no longer describes URL as item original link")
        shape = {
            "kind": "FLASH", "arguments": {}, "items_path": ["data", "items"],
            "cursor_argument": "cursor", "has_more_path": ["data", "has_more"],
            "next_cursor_path": ["data", "next_cursor"],
            "source_id_field": None, "source_id_stability_confirmed": False,
            "url_field": "url", "url_semantics_confirmed": True,
            "published_field": "time", "title_field": "title", "content_field": "content",
            "business_status_path": ["status"], "business_success_values": [200],
        }
        spec = ToolSpec(name=tool["name"], input_schema=input_schema,
                        **{key: tuple(value) if key.endswith("_path") and value is not None else value
                           for key, value in shape.items()})
        items, _, _, status, _ = parse_page(response, spec)
        if status != 200 or not items:
            raise ValueError("Flash witness is not a successful nonempty page")
        if not all(checked_source_url(item.get("url")) and
                   urlsplit(item["url"]).hostname == "flash.jin10.com" and
                   urlsplit(item["url"]).path.startswith("/detail/") for item in items):
            raise ValueError("Provider item URL shape was not confirmed")
    else:
        output = tool.get("outputSchema", {}).get("properties", {}).get("data", {})
        if output.get("type") != "array":
            raise ValueError("Live calendar output is not an array")
        item_properties = output.get("items", {}).get("properties", {})
        if not {"pub_time", "title", "actual", "consensus", "previous", "revised"}.issubset(item_properties):
            raise ValueError("Live calendar fields differ from observed response")
        if "理论公布时间" not in item_properties["pub_time"].get("description", ""):
            raise ValueError("Calendar pub_time semantics are not confirmed")
        shape = {
            "kind": "CALENDAR", "arguments": {}, "items_path": ["data"],
            "source_id_field": None, "source_id_stability_confirmed": False,
            "url_field": None, "url_semantics_confirmed": False,
            "published_field": "pub_time", "title_field": "title", "content_field": None,
            "business_status_path": ["status"], "business_success_values": [200],
        }
        spec = ToolSpec(name=tool["name"], input_schema=input_schema,
                        **{key: tuple(value) if key.endswith("_path") and value is not None else value
                           for key, value in shape.items()})
        items, _, _, status, _ = parse_page(response, spec)
        if status != 200 or not items:
            raise ValueError("Calendar witness is not a successful nonempty array")
    return {
        "schema": "jin10_confirmed_tool_shape_v1",
        "tool_name": tool["name"],
        "input_schema_sha256": sha_object(input_schema),
        "confirmed_response_sha256": sha_object(response),
        "confirmed_response_capture_file": capture_file,
        "confirmed_response_capture_index": 0,
        **shape,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--public-receipt", type=Path, required=True)
    args = parser.parse_args()
    private = args.private_dir.resolve()
    public = args.public_receipt.resolve()
    if not private.is_relative_to(PRIVATE_ROOT) or not public.is_relative_to(PUBLIC_ROOT):
        raise ValueError("Shapes stay private; only their hashes are public")
    if public.exists():
        raise FileExistsError(public)
    listing_path = private / "tools_list.json"
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    receipt = {"schema": "jin10_shape_binding_receipt_v1",
               "tools_list_sha256": sha_bytes(listing_path), "tools": {}}
    for name, kind, capture_name in (("list_flash", "FLASH", "flash_captures.json"),
                                     ("list_calendar", "CALENDAR", "calendar_captures.json")):
        capture_path = private / capture_name
        captures = json.loads(capture_path.read_text(encoding="utf-8"))
        if not isinstance(captures, list) or not captures:
            raise ValueError(f"No actual {name} capture")
        tool = tool_from_listing(listing, name)
        response = captures[0]["response"]
        decoded, _ = unwrap_mcp_payload(response)
        if decoded.get("status") != 200:
            raise ValueError(f"Actual {name} witness was not business-successful")
        shape = make_shape(tool, kind, response, capture_name)
        shape_path = private / f"{name}_shape.json"
        if shape_path.exists():
            raise FileExistsError(shape_path)
        shape_path.write_text(json.dumps(shape, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8", newline="\n")
        load_spec(shape_path, tool)
        receipt["tools"][name] = {
            "kind": kind, "input_schema_sha256": shape["input_schema_sha256"],
            "response_witness_sha256": shape["confirmed_response_sha256"],
            "shape_sha256": sha_bytes(shape_path),
            "capture_file_sha256": sha_bytes(capture_path),
            "url_identity_basis": ("PROVIDER_DESCRIBED_ORIGINAL_URL_AND_HTTPS_DETAIL_SHAPE"
                                   if kind == "FLASH" else "NO_URL_COMPOSITE_COLLISION_FLAGGED"),
        }
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    print(json.dumps({"receipt": public.relative_to(ROOT).as_posix(),
                      "confirmed_tools": sorted(receipt["tools"])}))


if __name__ == "__main__":
    main()
