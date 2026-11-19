#!/usr/bin/env bash
# Owner-run: store the Benzinga key(s) in /srv/quant/secrets/news.env (root:root 0600) without echoing them.
# Usage (interactive, on the server): sudo bash quant-set-news-key.sh
# Prints only the length and an 8-hex sha256 fingerprint of each value; never the value itself.
# Keeps any existing NEWS_PROVIDER line; the collector stays stopped until the operator starts it.
set -euo pipefail
umask 077
F=/srv/quant/secrets/news.env
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 2; }
[ -t 0 ] || { echo "needs an interactive terminal (ssh -t)"; exit 2; }
install -d -m 0700 -o root -g root /srv/quant/secrets

fp() { printf '%s' "$1" | sha256sum | cut -c1-8; }
read -rs -p "Benzinga API key (REST, input hidden): " REST; echo
[ -n "$REST" ] || { echo "empty; nothing written"; exit 1; }
case "$REST" in *[[:space:]]*|*\"*|*\'*|*'$'*|*'`'*) echo "unexpected characters; nothing written"; exit 1;; esac
read -rs -p "Separate WebSocket token if Benzinga issued one (Enter = use the same key): " WS; echo
case "$WS" in *[[:space:]]*|*\"*|*\'*|*'$'*|*'`'*) echo "unexpected characters; nothing written"; exit 1;; esac

PROVIDER_LINE=""
[ -f "$F" ] && PROVIDER_LINE=$(grep -E '^NEWS_PROVIDER=[a-z_]+$' "$F" || true)
TMP=$(mktemp /srv/quant/secrets/.news.env.XXXXXX)
{
  [ -n "$PROVIDER_LINE" ] && echo "$PROVIDER_LINE"
  echo "BENZINGA_TOKEN=$REST"
  [ -n "$WS" ] && echo "BENZINGA_WS_TOKEN=$WS"
} > "$TMP"
chmod 0600 "$TMP"; chown root:root "$TMP"; mv -f "$TMP" "$F"
echo "written $F (0600 root)"
echo "BENZINGA_TOKEN len=${#REST} fp=$(fp "$REST")"
[ -n "$WS" ] && echo "BENZINGA_WS_TOKEN len=${#WS} fp=$(fp "$WS")" || echo "BENZINGA_WS_TOKEN not set (stream will use the REST key)"
unset REST WS
