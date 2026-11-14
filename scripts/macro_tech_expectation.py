"""Compute a numerical release surprise only from dated, comparable inputs.

The current macro/technology candidate cards have no verified pre-release
consensus, so this module is exercised only on synthetic contract cases.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse


def _aware(value: str, name: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return parsed


def calculate_surprise(
    *,
    actual: str | int | float | None,
    actual_unit: str,
    pre_release_consensus: str | int | float | None,
    consensus_unit: str | None,
    consensus_as_of: str | None,
    release_at: str | None,
    actual_metric: str | None = None,
    actual_period: str | None = None,
    consensus_metric: str | None = None,
    consensus_period: str | None = None,
    consensus_source_url: str | None = None,
) -> dict:
    """Return `actual - pre_release_consensus` or an explicit non-computation.

    A dated consensus must precede the event's release or embargo lift.
    `release_at` is the official release instant, not a later data vintage.
    The caller must independently verify that the source really records the
    dated pre-release consensus. URL syntax and these mechanical gates do not
    verify source contents or an independent first-public instant.
    """
    if actual is None or pre_release_consensus is None:
        return {"status": "NOT_COMPUTED_MISSING_ACTUAL_OR_CONSENSUS", "value": None}
    if not actual_unit or actual_unit != consensus_unit:
        raise ValueError("Actual and consensus units must match exactly")
    if not actual_metric or actual_metric != consensus_metric:
        raise ValueError("Actual and consensus metrics must match exactly")
    if not actual_period or actual_period != consensus_period:
        raise ValueError("Actual and consensus periods must match exactly")
    parsed_source = urlparse(consensus_source_url or "")
    if parsed_source.scheme != "https" or not parsed_source.netloc:
        raise ValueError("Dated consensus requires an HTTPS source URL")
    if consensus_as_of is None or release_at is None:
        raise ValueError("Comparable values require dated consensus and release")
    if _aware(consensus_as_of, "consensus_as_of") >= _aware(release_at, "release_at"):
        raise ValueError("Consensus was not recorded before the release")
    actual_value = Decimal(str(actual))
    consensus_value = Decimal(str(pre_release_consensus))
    if not actual_value.is_finite() or not consensus_value.is_finite():
        raise ValueError("Actual and consensus must be finite numbers")
    value = actual_value - consensus_value
    return {"status": "COMPUTED", "value": str(value), "unit": actual_unit}
