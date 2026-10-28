"""Adjudicate a raw-versus-standalone 10-Q HTML byte difference without replacing either snapshot."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from finembed.run import sha256, write_json


FIXED = ("MSFT", "AMZN", "GOOGL", "META")
PREFIX = b"<XBRL>\n"
SUFFIX = b"</XBRL>"
SCRIPT = re.compile(rb'<script\s+type="text/javascript"\s+src="/[^"<>]+"\s*></script>', re.I)


def prove(prior: bytes, embedded: bytes) -> dict:
    """Require exact equivalence after one wrapper and one standalone script element."""
    if not embedded.startswith(PREFIX) or not embedded.endswith(SUFFIX):
        raise ValueError("Raw-embedded primary lacks the exact observed XBRL wrapper")
    inner = embedded[len(PREFIX):-len(SUFFIX)]
    common = 0
    for a, b in zip(prior, inner):
        if a != b:
            break
        common += 1
    if common < 1:
        raise ValueError("No common primary HTML prefix")
    suffix = 0
    while suffix < min(len(prior), len(inner)) - common and prior[-1-suffix] == inner[-1-suffix]:
        suffix += 1
    changed_prior = prior[common:len(prior)-suffix if suffix else len(prior)]
    changed_inner = inner[common:len(inner)-suffix if suffix else len(inner)]
    if changed_inner or not changed_prior.endswith(b"<"):
        raise ValueError("Difference is not exactly one prior-only script insertion")
    script = b"<" + changed_prior[:-1]
    if len(script) != 114 or not SCRIPT.fullmatch(script):
        raise ValueError("Prior-only insertion is not the observed 114-byte external script element")
    if prior.count(script) != 1 or prior.replace(script, b"", 1) != inner:
        raise ValueError("Removing exactly one prior script does not reproduce raw-embedded HTML")
    return {"status": "PASS_STRUCTURAL_EQUIVALENCE",
            "rule": "XBRL_WRAPPER_AND_ONE_STANDALONE_SCRIPT_ELEMENT",
            "exact_unwrapped_bytes_equal": True,
            "wrapper_prefix_bytes": len(PREFIX), "wrapper_suffix_bytes": len(SUFFIX),
            "script_bytes": len(script), "script_sha256": sha256(script),
            "script_insert_byte_start_in_prior": common - 1,
            "prior_removed_script_sha256": sha256(prior.replace(script, b"", 1)),
            "raw_embedded_unwrapped_sha256": sha256(inner),
            "script_origin_or_function": "UNVERIFIED; not executed"}


def run(run_dir: Path, candidates_path: Path) -> dict:
    run_dir = run_dir.resolve(strict=True)
    ledger_bytes = candidates_path.resolve(strict=True).read_bytes()
    ledger = json.loads(ledger_bytes)
    rows = {row["ticker"]: row for row in ledger["rows"]}
    output = run_dir / "version_adjudication"
    output.mkdir(exist_ok=False)
    results = []
    for ticker in FIXED:
        row = rows[ticker]
        identity_path = run_dir / "ingest" / ticker / "source_identity.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        receipt = {"ticker": ticker, "cik": row["cik"], "accession": row["accession"],
                   "created_at_utc": datetime.now(timezone.utc).isoformat(),
                   "candidate_ledger_sha256": sha256(ledger_bytes),
                   "acquisition_identity_sha256": sha256(identity_path.read_bytes()),
                   "status": "NEEDS_VERSION_REVIEW"}
        try:
            if (identity.get("status") != "NEEDS_VERSION_REVIEW" or
                identity.get("ticker") != ticker or identity.get("cik") != row["cik"] or
                identity.get("accession") != row["accession"]):
                raise ValueError("Identity/status differs from fixed version-review candidate")
            raw_path = run_dir / "ingest" / ticker / "original_submission.txt"
            embedded_path = run_dir / "ingest" / ticker / "primary_document_source.html"
            prior_path = Path(row["primary_html"]["path"]).resolve(strict=True)
            prior_root = candidates_path.resolve(strict=True).parent
            if not prior_path.is_relative_to(prior_root):
                raise ValueError("Prior HTML path outside fixed candidate run")
            prior, embedded = prior_path.read_bytes(), embedded_path.read_bytes()
            if (sha256(raw_path.read_bytes()) != identity["original_submission_sha256"] or
                sha256(embedded) != identity["primary_document_sha256"] or
                sha256(prior) != row["primary_html"]["sha256"]):
                raise ValueError("One of the three preserved source hashes changed")
            proof = prove(prior, embedded)
            receipt.update({"new_raw_sha256": sha256(raw_path.read_bytes()),
                            "new_embedded_primary_sha256": sha256(embedded),
                            "prior_standalone_primary_sha256": sha256(prior),
                            "prior_standalone_path": str(prior_path),
                            "equivalence_proof": proof,
                            "status": "PROCEED_WITH_NEW_RAW_SNAPSHOT_FOR_UNREVIEWED_PILOT",
                            "interpretation": "Only exact wrapper/script structural difference established; use the new complete raw filing as source. No human content decision or historical public-time claim follows."})
        except Exception as exc:
            receipt["status"] = "NEEDS_VERSION_REVIEW"
            receipt["reason"] = f"{type(exc).__name__}: {exc}"
        path = output / f"{ticker}.json"
        if path.exists():
            raise FileExistsError(path)
        write_json(path, receipt)
        results.append({"ticker": ticker, "status": receipt["status"],
                        "receipt": str(path), "receipt_sha256": sha256(path.read_bytes()),
                        "reason": receipt.get("reason")})
    summary = {"kind": "M8_PRIMARY_VERSION_ADJUDICATION", "rows": results,
               "all_proceed": all(row["status"] == "PROCEED_WITH_NEW_RAW_SNAPSHOT_FOR_UNREVIEWED_PILOT" for row in results)}
    write_json(output / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.run, args.candidates)
    print(json.dumps(result))
    return 0 if result["all_proceed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
