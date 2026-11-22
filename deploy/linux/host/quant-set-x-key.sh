#!/usr/bin/env bash
# Owner-run: store the TwitterAPI.io key in /srv/quant/secrets/x_stance.env (root:root 0600) without echoing it.
# Usage (interactive, on the server): sudo bash quant-set-x-key.sh
# Prints only the length and an 8-hex sha256 fingerprint; never the value. Nothing is requested from the provider
# here, and the daily job stays off until a preflight has passed (quant-x-stance.sh preflight).
set -euo pipefail
umask 077
F=/srv/quant/secrets/x_stance.env
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 2; }
[ -t 0 ] || { echo "needs an interactive terminal (ssh -t)"; exit 2; }
install -d -m 0700 -o root -g root /srv/quant/secrets
read -rs -p "TwitterAPI.io API key (input hidden): " KEY; echo
[ -n "$KEY" ] || { echo "empty; nothing written"; exit 1; }
if ! printf '%s' "$KEY" | grep -Eq '^[A-Za-z0-9_.-]+$'; then echo "unexpected characters; nothing written"; exit 1; fi
T=$(mktemp "$F.XXXXXX")
printf 'TWITTERAPI_IO_API_KEY=%s\n' "$KEY" > "$T"
chown root:root "$T"; chmod 0600 "$T"; mv -f "$T" "$F"
echo "stored: length=${#KEY} sha256_prefix=$(printf '%s' "$KEY" | sha256sum | cut -c1-8) file=$F"
unset KEY
