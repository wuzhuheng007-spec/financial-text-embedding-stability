"""Conservative retrospective event-to-session alignment.

The output is a diagnostic candidate ledger. A publication timestamp is not
evidence that this observer possessed the item before a historical decision.
Only an independently evidenced ``first_available_at`` can make a row eligible
for strict point-in-time modelling, and present-day retrieval never substitutes
for that field.
"""

from __future__ import annotations

import string
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


US_EASTERN = ZoneInfo("America/New_York")


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in string.hexdigits for char in value)


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp has no explicit UTC offset")
    return parsed


def align_event(event: dict, trading_dates: list[date], *,
                end: date, decision_hour_et: int = 18) -> dict:
    """Map a dated report to the first later US session, retaining uncertainty.

    Date-only items need a documented local publication calendar date and
    ``source_timezone``. For timestamped items the explicit offset is used.
    The one-session lag avoids assigning an event to an earlier same-day close.
    It does not by itself prove historical first availability.
    """

    required = ("event_id", "source_type", "source_hash", "retrieved_at")
    if any(not event.get(key) for key in required):
        raise ValueError("event identity, source hash and retrieval time required")
    if not _digest(event["source_hash"]):
        raise ValueError("source hash must be SHA-256")
    retrieved = _aware(str(event["retrieved_at"]))
    precision = str(event.get("time_precision", "UNKNOWN"))
    pub_time = event.get("published_at")
    pub_date = event.get("published_date")
    zone_name = event.get("source_timezone")
    if pub_time:
        published = _aware(str(pub_time))
        local_date = published.astimezone(US_EASTERN).date()
    elif pub_date and precision == "DATE" and zone_name:
        # A source's calendar date is usable only when its reporting timezone
        # is explicit. No intraday time is invented.
        ZoneInfo(str(zone_name))
        local_date = date.fromisoformat(str(pub_date))
        published = None
    else:
        return {
            "event_id": event["event_id"], "status": "UNALIGNED_TIME_UNCLEAR",
            "decision_date": None, "strict_pit_eligible": False,
            "reason": "publication date, precision or timezone is insufficient",
        }
    if published is not None and published > retrieved:
        raise ValueError("retrieval precedes purported publication")
    ordered = sorted(set(trading_dates))
    if len(ordered) != len(trading_dates):
        raise ValueError("duplicate trading date")
    aligned = next((day for day in ordered if local_date < day <= end), None)
    if aligned is None:
        return {
            "event_id": event["event_id"], "status": "NO_LATER_SESSION_IN_WINDOW",
            "publication_date_et": local_date.isoformat(), "decision_date": None,
            "strict_pit_eligible": False, "reason": "no later bounded session",
        }
    cutoff = datetime.combine(aligned, time(decision_hour_et), US_EASTERN)
    first_available = event.get("first_available_at")
    availability_hash = event.get("availability_evidence_hash")
    if first_available is not None and published is not None and \
            _aware(str(first_available)) < published:
        raise ValueError("first availability precedes source publication")
    proven = bool(first_available and _digest(availability_hash) and
                  _aware(str(first_available)) <= cutoff)
    return {
        "event_id": event["event_id"],
        "source_type": event["source_type"],
        "source_hash": event["source_hash"],
        "publication_date_et": local_date.isoformat(),
        "time_precision": precision,
        "decision_date": aligned.isoformat(),
        "decision_cutoff_utc": cutoff.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PIT_VERIFIED" if proven else "PUBLICATION_PROXY_ONLY",
        "strict_pit_eligible": proven,
        "reason": "independent first-availability evidence" if proven
        else "historical first availability not independently evidenced",
    }


def align_events(events: list[dict], trading_dates: list[date], *, end: date) -> list[dict]:
    ids = [str(item.get("event_id")) for item in events]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate event ID")
    return [align_event(item, trading_dates, end=end) for item in events]
