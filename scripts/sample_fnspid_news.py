"""Take a bounded, anonymous first-record sample of the pinned upstream FNSPID CSV.

Raw rows and the local manifest must remain below this repository's ignored
``artifacts/`` directory. This is an engineering sample, not a financial cohort.
The 2026-11-14 acquisition used an earlier exact script retained locally; its
manifest records that script's hash and a separate, reserved Range preflight.
"""

from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


REVISION = "bf9189c41527198897d1af3e17b1a0095279fc45"
TARGET_FILE = "Stock_news/nasdaq_exteral_data.csv"  # upstream spelling
URL = f"https://huggingface.co/datasets/Zihan1004/FNSPID/resolve/{REVISION}/{TARGET_FILE}"
GITHUB_REVISION = "4054842ec476953b30ee874d4b7e8eea786a21fa"
MAX_ROWS = 1000
MAX_BODIES = 100
MAX_BYTES = 50 * 1024 * 1024
MAX_PHYSICAL_LINE_CHARS = 10 * 1024 * 1024
NULLISH_BODIES = {"", "nan", "null", "none", "0", "2", "3"}
USER_AGENT = "quant-news-bounded-sample/1.1"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_usable_body(value: str) -> bool:
    return value.strip().lower() not in NULLISH_BODIES


def complete_physical_lines(chunks, meter: dict, max_bytes: int, *,
                            range_is_prefix: bool = True):
    """Feed CSV physical lines, distinguishing a byte prefix from real EOF.

    A Range prefix cannot certify its final physical line, so withhold that
    line. At complete-file EOF, finalize UTF-8 and pass a legal unterminated
    final line to ``csv.reader``. The reader joins quoted multiline records.
    """
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    pending = ""
    for chunk in chunks:
        if meter["bytes_consumed"] + len(chunk) > max_bytes:
            raise RuntimeError("CLIENT_TRANSFER_LIMIT")
        meter["bytes_consumed"] += len(chunk)
        meter["prefix_sha256"].update(chunk)
        pending += decoder.decode(chunk)
        while True:
            end = pending.find("\n")
            if end < 0:
                break
            line = pending[: end + 1]
            if len(line) > MAX_PHYSICAL_LINE_CHARS:
                raise RuntimeError("PHYSICAL_LINE_TOO_LONG")
            yield line
            pending = pending[end + 1 :]
        if len(pending) > MAX_PHYSICAL_LINE_CHARS:
            raise RuntimeError("PHYSICAL_LINE_TOO_LONG")
    meter["trailing_incomplete_utf8_bytes"] = len(decoder.getstate()[0])
    if range_is_prefix:
        meter["trailing_incomplete_physical_line_chars"] = len(pending)
        return
    pending += decoder.decode(b"", final=True)
    if pending:
        if len(pending) > MAX_PHYSICAL_LINE_CHARS:
            raise RuntimeError("PHYSICAL_LINE_TOO_LONG")
        meter["final_unterminated_physical_line_chars"] = len(pending)
        yield pending
    meter["trailing_incomplete_physical_line_chars"] = 0


def parse_csv_prefix(chunks, sample_path: Path, *, max_rows: int, max_bodies: int,
                     max_bytes: int, range_is_prefix: bool = True,
                     state: dict | None = None) -> tuple[dict, dict]:
    """Parse bounded bytes and retain original CSV field values, in order."""
    previous_field_limit = csv.field_size_limit(max_bytes)
    try:
        return _parse_csv_prefix_impl(
            chunks, sample_path, max_rows=max_rows, max_bodies=max_bodies,
            max_bytes=max_bytes, range_is_prefix=range_is_prefix, state=state)
    finally:
        csv.field_size_limit(previous_field_limit)


def _parse_csv_prefix_impl(chunks, sample_path: Path, *, max_rows: int,
                           max_bodies: int, max_bytes: int, range_is_prefix: bool,
                           state: dict | None) -> tuple[dict, dict]:
    meter = {"bytes_consumed": 0, "prefix_sha256": hashlib.sha256(),
             "trailing_incomplete_physical_line_chars": None,
             "trailing_incomplete_utf8_bytes": None,
             "final_unterminated_physical_line_chars": 0}
    result = {"header": None, "complete_logical_rows_scanned": 0,
              "saved_nonempty_body_rows": 0, "rows_with_width_mismatch": 0,
              "rows_with_empty_or_sentinel_body": 0, "stop_reason": None}
    if state is not None:
        state.update(result=result, meter=meter)
    reader = csv.reader(complete_physical_lines(
        chunks, meter, max_bytes, range_is_prefix=range_is_prefix), strict=True)
    header = next(reader)
    result["header"] = header
    if len(header) != len(set(header)) or "Article" not in header:
        raise ValueError("INVALID_OR_MISSING_ARTICLE_HEADER")
    body_index = header.index("Article")
    with sample_path.open("x", encoding="utf-8", newline="\n") as out:
        try:
            for row in reader:
                result["complete_logical_rows_scanned"] += 1
                ordinal = result["complete_logical_rows_scanned"]
                if len(row) != len(header):
                    result["rows_with_width_mismatch"] += 1
                elif is_usable_body(row[body_index]):
                    out.write(json.dumps({"source_row_ordinal": ordinal, "fields": row},
                                         ensure_ascii=False, separators=(",", ":")) + "\n")
                    result["saved_nonempty_body_rows"] += 1
                else:
                    result["rows_with_empty_or_sentinel_body"] += 1
                if result["saved_nonempty_body_rows"] >= max_bodies:
                    result["stop_reason"] = "SAVED_BODY_LIMIT"
                    break
                if ordinal >= max_rows:
                    result["stop_reason"] = "LOGICAL_ROW_LIMIT"
                    break
        except csv.Error as exc:
            if (range_is_prefix and meter["bytes_consumed"] == max_bytes
                    and "unexpected end of data" in str(exc).lower()):
                result["stop_reason"] = (
                    "RANGE_END_IN_PARTIAL_UTF8_SEQUENCE"
                    if meter["trailing_incomplete_utf8_bytes"] else
                    "RANGE_END_IN_PARTIAL_QUOTED_RECORD"
                )
            else:
                raise
    if result["stop_reason"] is None:
        if range_is_prefix:
            if meter["trailing_incomplete_utf8_bytes"]:
                result["stop_reason"] = "RANGE_END_IN_PARTIAL_UTF8_SEQUENCE"
            elif meter["trailing_incomplete_physical_line_chars"]:
                result["stop_reason"] = "RANGE_END_IN_PARTIAL_PHYSICAL_LINE"
            else:
                result["stop_reason"] = "RANGE_EXHAUSTED_BEFORE_ROW_OR_BODY_LIMIT"
        else:
            result["stop_reason"] = "SOURCE_EOF_BEFORE_ROW_OR_BODY_LIMIT"
    meter["prefix_sha256"] = meter["prefix_sha256"].hexdigest()
    return result, meter


def parse_content_range(value: str, expected_total: int, requested_bytes: int) -> int:
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", value)
    if not match:
        raise RuntimeError("RANGE_RESPONSE_MISSING_OR_INVALID")
    start, end, total = map(int, match.groups())
    if start != 0 or total != expected_total or end != min(total, requested_bytes) - 1:
        raise RuntimeError("RANGE_RESPONSE_MISMATCH")
    return end + 1


def check_output_dir(path: Path) -> Path:
    artifacts = (Path(__file__).resolve().parents[1] / "artifacts").resolve()
    target = path.resolve()
    if target == artifacts or not target.is_relative_to(artifacts):
        raise ValueError("OUTPUT_MUST_BE_INSIDE_IGNORED_ARTIFACTS")
    if target.exists():
        raise FileExistsError(f"Output already exists: {target}")
    return target


def transfer_record(meter: dict, advertised_bytes: int) -> dict:
    digest = meter["prefix_sha256"]
    return {"main_bytes_consumed": meter["bytes_consumed"],
            "consumed_prefix_sha256": digest.hexdigest() if hasattr(digest, "hexdigest") else digest,
            "advertised_range_budget_bytes": advertised_bytes,
            "trailing_incomplete_physical_line_chars":
            meter["trailing_incomplete_physical_line_chars"],
            "trailing_incomplete_utf8_bytes": meter["trailing_incomplete_utf8_bytes"],
            "final_unterminated_physical_line_chars":
            meter["final_unterminated_physical_line_chars"]}


def run(output_dir: Path, *, max_rows: int = MAX_ROWS, max_bodies: int = MAX_BODIES,
        max_bytes: int = MAX_BYTES, session=None) -> dict:
    if not (1 <= max_rows <= MAX_ROWS and 1 <= max_bodies <= MAX_BODIES
            and 1 <= max_bytes <= MAX_BYTES):
        raise ValueError("LIMIT_EXCEEDS_APPROVED_BUDGET")
    target = check_output_dir(output_dir)
    target.mkdir(parents=True)
    sample_path = target / "raw_fields_with_body.jsonl"
    manifest_path = target / "local_manifest.json"
    owned_session = session is None
    session = session or requests.Session()
    session.trust_env = False  # no ambient proxies or .netrc credentials
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    manifest = {
        "schema": "fnspid_bounded_local_sample_v2",
        "started_at_utc": now_utc(),
        "source": {"dataset": "Zihan1004/FNSPID", "revision": REVISION,
                   "target_file": TARGET_FILE, "canonical_url": URL,
                   "card_url": f"https://huggingface.co/datasets/Zihan1004/FNSPID/blob/{REVISION}/README.md",
                   "license_url": f"https://github.com/Zdong104/FNSPID_Financial_News_Dataset/blob/{GITHUB_REVISION}/LICENSE",
                   "access": "ANONYMOUS_RANGE_GET", "redistribution": "RAW_LOCAL_ONLY"},
        "limits": {"logical_rows": max_rows, "saved_nonempty_body_rows": max_bodies,
                   "transfer_bytes": max_bytes},
        "request_policy": {"head_max_attempts": 2, "get_max_attempts_before_body": 1,
                           "retry_after_body": False, "connect_timeout_seconds": 10,
                           "read_timeout_seconds": 30, "trust_env": False,
                           "authorization_header": False},
        "result": {"stop_reason": "NOT_STARTED"},
    }
    parse_state: dict = {}
    requested_bytes = 0
    try:
        head = None
        for attempt in range(2):
            try:
                head = session.head(URL, headers=headers, allow_redirects=False,
                                    timeout=(10, 30))
            except requests.RequestException:
                if attempt == 1:
                    raise
                continue
            if head.status_code < 500 or attempt == 1:
                break
            head.close()
        if head is None or head.status_code not in (200, 301, 302, 303, 307, 308):
            raise RuntimeError(f"HEAD_HTTP_{head.status_code if head is not None else 'NO_RESPONSE'}")
        source = manifest["source"]
        source["head_status"] = head.status_code
        source["head_repo_commit"] = head.headers.get("X-Repo-Commit")
        source["linked_size_bytes"] = head.headers.get("X-Linked-Size")
        source["linked_etag"] = head.headers.get("X-Linked-Etag")
        source["redirect_host"] = urlparse(head.headers.get("Location", "")).hostname
        head.close()
        if source["head_repo_commit"] != REVISION:
            raise RuntimeError("HEAD_REVISION_MISMATCH")
        try:
            linked_size = int(source["linked_size_bytes"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("HEAD_SIZE_MISSING") from exc
        if linked_size < 1:
            raise RuntimeError("HEAD_SIZE_INVALID")
        requested_bytes = min(max_bytes, linked_size)
        source["requested_range"] = f"bytes=0-{requested_bytes - 1}"
        get_headers = dict(headers, Range=source["requested_range"])
        response = None
        response = session.get(URL, headers=get_headers, stream=True,
                               allow_redirects=True, timeout=(10, 30))
        if response is None:
            raise RuntimeError("RANGE_GET_NO_RESPONSE")
        with response:
            source["get_status"] = response.status_code
            source["response_content_range"] = response.headers.get("Content-Range")
            source["response_content_length"] = response.headers.get("Content-Length")
            source["response_content_encoding"] = response.headers.get("Content-Encoding")
            source["final_host"] = urlparse(response.url).hostname  # omit signed query
            if response.status_code != 206:
                raise RuntimeError(f"RANGE_GET_HTTP_{response.status_code}")
            range_length = parse_content_range(source["response_content_range"] or "",
                                               linked_size, requested_bytes)
            if (response.headers.get("Content-Encoding") not in (None, "identity")
                    or response.headers.get("Content-Length") != str(range_length)):
                raise RuntimeError("RANGE_LENGTH_OR_ENCODING_MISMATCH")
            result, meter = parse_csv_prefix(response.iter_content(chunk_size=64 * 1024),
                                             sample_path, max_rows=max_rows,
                                             max_bodies=max_bodies, max_bytes=max_bytes,
                                             range_is_prefix=linked_size > requested_bytes,
                                             state=parse_state)
            manifest["result"] = result
            manifest["transfer"] = transfer_record(meter, requested_bytes)
    except Exception as exc:
        if parse_state:
            manifest["result"] = dict(parse_state["result"])
            manifest["transfer"] = transfer_record(parse_state["meter"], requested_bytes)
        manifest["result"]["stop_reason"] = "ERROR"
        manifest["result"]["error_type"] = type(exc).__name__
        manifest["result"]["error_message"] = (
            "REQUEST_FAILED" if isinstance(exc, requests.RequestException)
            else str(exc)[:300]
        )
    finally:
        manifest["finished_at_utc"] = now_utc()
        if sample_path.exists():
            manifest["sample"] = {"local_relative_path": sample_path.name,
                                  "bytes": sample_path.stat().st_size,
                                  "sha256": sha256_file(sample_path),
                                  "format": "JSONL of source CSV field values, not byte-identical CSV slices"}
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
        if owned_session:
            session.close()
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="new directory under this repository's ignored artifacts/")
    parser.add_argument("--max-rows", type=int, default=MAX_ROWS)
    parser.add_argument("--max-bodies", type=int, default=MAX_BODIES)
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    args = parser.parse_args()
    record = run(args.output_dir, max_rows=args.max_rows,
                 max_bodies=args.max_bodies, max_bytes=args.max_bytes)
    print(json.dumps({"stop_reason": record["result"]["stop_reason"],
                      "rows": record["result"].get("complete_logical_rows_scanned"),
                      "saved_bodies": record["result"].get("saved_nonempty_body_rows"),
                      "bytes_consumed": record.get("transfer", {}).get("main_bytes_consumed")},
                     ensure_ascii=False))
    return int(record["result"]["stop_reason"] == "ERROR")


if __name__ == "__main__":
    raise SystemExit(main())
