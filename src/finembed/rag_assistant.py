"""Answer from a local validated RAG index, or return source-bound passages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .generation import GenerationError, answer_from_evidence, load_generation_backend
from .rag import RagError, retrieve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--mode", choices=("bm25", "dense", "hybrid"), required=True)
    parser.add_argument("--backend-config", type=Path,
                        default=Path("configs/generation_none.json"))
    parser.add_argument("--ticker")
    parser.add_argument("--accession")
    parser.add_argument("--section")
    parser.add_argument("--version")
    parser.add_argument("--top-k", type=int)
    args = parser.parse_args(argv)
    try:
        retrieval = retrieve(args.index, args.query, args.mode,
                             ticker=args.ticker, accession=args.accession,
                             section=args.section, version=args.version,
                             top_k=args.top_k)
        backend, backend_status = load_generation_backend(args.backend_config)
        answer = answer_from_evidence(args.query, retrieval, backend)
    except (RagError, GenerationError, ValueError, FileNotFoundError, KeyError) as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__,
                          "reason": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"retrieval_status": retrieval["status"],
                      "retrieval_mode": args.mode,
                      "index_manifest_sha256": retrieval["index_manifest_sha256"],
                      "backend": backend_status,
                      "response": answer}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
