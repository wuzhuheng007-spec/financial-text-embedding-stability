#!/bin/bash
# Recompute every receipt-1 table from the frozen private inputs (no network, nothing written outside the two dirs).
#   bash scripts/qqq_x_10y/recompute.sh <market_research.sqlite> <private dir holding store_export/> <public out dir>
# Inputs are hash-checked: the price snapshot inside prices.load, the store export against store_export/export.tar.gz.sha256.
set -euo pipefail
SNAP=$1; PRIV=$2; PUB=$3
( cd "$PRIV/store_export" && sha256sum -c --quiet export.tar.gz.sha256 )
python -m pytest -q -p no:cacheprovider tests/test_qqq_x_10y_regimes.py tests/test_qqq_x_10y_signals.py
python -m scripts.qqq_x_10y.build_regimes --snapshot "$SNAP" --private "$PRIV" --public "$PUB"
python -m scripts.qqq_x_10y.build_receipt1 --export "$PRIV/store_export" --regimes "$PRIV/regime_daily_private.csv" --private "$PRIV" --public "$PUB"
python -m scripts.qqq_x_10y.build_signals --snapshot "$SNAP" --private "$PRIV" --public "$PUB"
python -I scripts/qqq_x_10y/verify_independent.py "$SNAP" "$PUB"
( cd "$PUB" && sha256sum *.csv *.json *.svg )
