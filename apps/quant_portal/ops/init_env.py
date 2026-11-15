"""Create a private Compose .env without printing or overwriting secrets."""

from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / ".env"
TEMPLATE = ROOT / ".env.example"


def main() -> None:
    parser = argparse.ArgumentParser()
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--local", action="store_true", help="loopback HTTP smoke test only")
    scope.add_argument("--domain", help="production DNS name with HTTPS")
    args = parser.parse_args()
    if TARGET.exists():
        raise SystemExit(".env already exists; refusing to overwrite")
    if args.domain and not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{1,251}[A-Za-z0-9])?", args.domain):
        raise SystemExit("Use a plain DNS name, without scheme or path")
    if args.domain and ("." not in args.domain or args.domain.endswith(".invalid")):
        raise SystemExit("A routable DNS name is required for the production profile")
    settings = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(48),
        "SECRET_KEY": secrets.token_urlsafe(64),
        "DEBUG": "1" if args.local else "0",
        "ALLOWED_HOSTS": "127.0.0.1,localhost" if args.local else args.domain,
        "CSRF_TRUSTED_ORIGINS": "http://127.0.0.1:8080" if args.local else f"https://{args.domain}",
        "SITE_DOMAIN": "portal.example.invalid" if args.local else args.domain,
    }
    lines = []
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={settings[key]}" if key in settings else line)
    fd = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
    except BaseException:
        TARGET.unlink(missing_ok=True)
        raise
    print(f"Created private {TARGET.name}; values were not printed")


if __name__ == "__main__":
    main()
