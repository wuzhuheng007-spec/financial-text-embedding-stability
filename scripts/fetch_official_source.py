"""Boundedly save one public primary-source document for local research audit.

The saved bytes and receipt belong under ignored local artifacts. A hash proves
the captured bytes, not that the page had the same content at publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def fetch(url: str, output: Path, receipt: Path, max_bytes: int, timeout: int) -> dict:
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS primary-source documents are accepted")
    if max_bytes <= 0 or max_bytes > 50 * 1024 * 1024 or timeout <= 0:
        raise ValueError("Invalid bounded fetch limits")
    if output.exists() or receipt.exists():
        raise FileExistsError("Preserve existing source captures and receipts")
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; research-document-audit/1.0)",
        "Accept": "text/html,application/pdf,application/xhtml+xml,*/*;q=0.8",
    })
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > max_bytes:
                    raise ValueError("Declared document size exceeds bound")
                content = response.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise ValueError("Document exceeds bound")
                final_url = response.geturl()
                if not final_url.startswith("https://"):
                    raise ValueError("Source redirected outside HTTPS")
                content_type = response.headers.get("Content-Type", "UNKNOWN")
                status = response.status
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt == 1 or (isinstance(error, urllib.error.HTTPError) and error.code < 500):
                raise
            time.sleep(1)
    else:
        assert last_error is not None
        raise last_error
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    result = {
        "schema": "official_source_capture_v1",
        "requested_url": url,
        "final_url": final_url,
        "http_status": status,
        "content_type": content_type,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "local_path": str(output),
        "limit_bytes": max_bytes,
        "interpretation": "Current capture only; not an archived release-time snapshot",
    }
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args()
    print(json.dumps(fetch(args.url, args.output, args.receipt,
                           args.max_bytes, args.timeout), ensure_ascii=False))


if __name__ == "__main__":
    main()
