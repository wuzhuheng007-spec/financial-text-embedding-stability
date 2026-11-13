"""Command line entry point for the M5 bridge."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("usage: python -m m5_bridge {diagnose,evaluate,verify-output} ...")
        return 0 if args else 2
    command, rest = args[0], args[1:]
    if command == "diagnose":
        from .diagnose import main as diagnose_main

        return diagnose_main(rest)
    if command == "evaluate":
        from .evaluate import main_evaluate

        return main_evaluate(rest)
    if command == "verify-output":
        from .evaluate import main_verify_output

        return main_verify_output(rest)
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
