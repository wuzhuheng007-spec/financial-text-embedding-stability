"""Redacted acceptance receipt for the first due quote period (2026-11-27 pre-market, from 09:00Z = Beijing 17:00).

Denominator: the confirmed subscription set read at Beijing 16:59 (baseline) is fixed. The 17:11 review is matched to it
subscription by subscription through HMAC keys whose salt never leaves the observing machine, and every baseline
subscription ends in exactly one state:
  RECEIVED                    still subscribed and got a valid pre-market quote in [09:00Z, 09:11Z)   (counts)
  SUBSCRIBED_NO_VALID_QUOTE   still subscribed, no valid pre-market quote                              (does not count)
  DROPPED                     no longer a confirmed subscription at review                             (does not count)
Subscriptions confirmed at review but not at baseline are NEW_SUBSCRIPTION, listed apart, outside the denominator.

Failure classes (any combination):
  OBSERVATION_MISSED   the 16:59 baseline or the live health samples are missing / late / too sparse; the quote
                       evidence then comes from a read-only cache look-back and the receipt says live health is missing
  FEED_NO_VALID_QUOTE  no baseline subscription received a valid pre-market quote
  RECEIPT_PUSH_FAILED  added by observe.sh (run_status.json) when the commit or push fails; the receipt stays local
Prints no symbol, key, quote value, account or server address.

CLI: python receipt.py DIR [--backfill]"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
START = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
LIVE_END = datetime(2026, 11, 27, 9, 12, tzinfo=UTC)
BASELINE_LATEST = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)       # a baseline taken after 17:00 is not a 16:59 baseline
MAX_LATE_FIRST_SAMPLE_S = 60
MAX_SAMPLE_GAP_S = 120
WATCHED = ("WAITING_FIRST_PUSH", "OK", "NO_FIRST_PUSH")


def _t(s):
    if not s:
        return None
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def health_coverage(health: list[dict]) -> dict:
    good = sorted(_t(h["t"]) for h in health if h.get("business") not in (None, "READ_ERROR") and START <= _t(h["t"]) < LIVE_END)
    if not good:
        return {"samples_ok": 0, "read_errors": sum(1 for h in health if h.get("business") == "READ_ERROR"), "complete": False, "reason": "NO_LIVE_SAMPLES"}
    edges = [START] + good + [LIVE_END]
    gap = max((b - a).total_seconds() for a, b in zip(edges, edges[1:]))
    late = (good[0] - START).total_seconds()
    reason = ("FIRST_SAMPLE_LATE" if late > MAX_LATE_FIRST_SAMPLE_S else "SAMPLE_GAP" if gap > MAX_SAMPLE_GAP_S else None)
    return {"samples_ok": len(good), "read_errors": sum(1 for h in health if h.get("business") == "READ_ERROR"),
            "first_sample_after_start_seconds": round(late, 1), "largest_gap_seconds": round(gap, 1), "complete": reason is None, "reason": reason}


def health_timeline(health: list[dict]) -> tuple[dict, list, list]:
    first, trans, prev = {}, [], object()
    for h in health:
        b = h.get("business")
        first.setdefault(b, h["t"])
        if b != prev:
            trans.append({"at_utc": h["t"], "business": b, "ok": h.get("ok"), "state": h.get("state"),
                          "grace_left_seconds": h.get("grace_left_seconds"), "down_seconds": h.get("down_seconds")})
            prev = b
    return {k: first.get(k) for k in WATCHED}, sorted(k for k in first if k not in WATCHED and k is not None), trans


def match(baseline_ev: dict | None, review: dict) -> dict:
    """Fixed denominator from the baseline; per-subscription matching by key."""
    rev_by_key = {v.get("key"): v for v in review["per_subscription"].values()}
    keyed = bool(review.get("keyed")) and None not in rev_by_key
    base_ok = bool(baseline_ev and baseline_ev.get("keyed") and keyed)
    if base_ok:
        base = sorted(baseline_ev["per_subscription"].items(), key=lambda kv: int(kv[0][1:]))
        denom_src = "BASELINE_1659_CONFIRMED_SET"
    else:                                             # no usable baseline: the review set is the only set there is
        base = sorted(review["per_subscription"].items(), key=lambda kv: int(kv[0][1:]))
        denom_src = "REVIEW_SET_BASELINE_MISSING" if not baseline_ev else "REVIEW_SET_BASELINE_UNKEYED"
    late_valid = review.get("valid_quote_first_by_unconfirmed_key", {})
    rows, base_keys = {}, set()
    for label, b in base:
        k = b.get("key")
        base_keys.add(k)
        r = rev_by_key.get(k) if base_ok else b
        if r is None:
            rows[label] = {"state": "DROPPED", "valid_quote_before_drop_utc": late_valid.get(k)}
        elif r["first_valid"]:
            rows[label] = {"state": "RECEIVED", "first_valid": r["first_valid"]}
        else:
            rows[label] = {"state": "SUBSCRIBED_NO_VALID_QUOTE", "excluded": r["excluded"]}
    new = {}
    if base_ok:
        extra = sorted((v for v in review["per_subscription"].values() if v.get("key") not in base_keys), key=lambda v: v["key"])
        for i, v in enumerate(extra):
            new[f"N{i + 1}"] = {"state": "NEW_SUBSCRIPTION", "first_valid": v["first_valid"]}
    count = {s: sum(1 for r in rows.values() if r["state"] == s) for s in ("RECEIVED", "SUBSCRIBED_NO_VALID_QUOTE", "DROPPED")}
    return {"denominator": len(rows), "denominator_source": denom_src, "per_subscription": rows, "new_subscriptions": new,
            "received": count["RECEIVED"], "subscribed_no_valid_quote": count["SUBSCRIBED_NO_VALID_QUOTE"],
            "dropped": count["DROPPED"], "new": len(new)}


def build(*, baseline: dict | None, review: dict, health: list[dict], backfill: bool = False) -> dict:
    m = match((baseline or {}).get("evidence") or None, review)
    cov = health_coverage(health)
    base_t = _t((baseline or {}).get("taken_utc"))
    missing = []
    if baseline is None or base_t is None or base_t >= BASELINE_LATEST:
        missing.append("BASELINE_1659")
    if not cov["complete"]:
        missing.append("LIVE_HEALTH")
    first, other, trans = health_timeline(health)
    n, got = m["denominator"], m["received"]
    classes = (["OBSERVATION_MISSED"] if missing else []) + (["FEED_NO_VALID_QUOTE"] if got == 0 else [])
    ev = review.get("state_events", [])
    return {
        "schema": "feed_first_due_period_receipt_v2", "feed_image": "quant-personal-app:feed-35a2471",
        "process": "existing feed container, no restart, no schedule change",
        "due_period": "2026-11-27 pre-market (XNYS EARLY_CLOSE day), due from 2026-11-27T09:00:00Z (Beijing 17:00)",
        "result": (f"{got}/{n} confirmed subscriptions (fixed at Beijing 16:59) received a valid pre-market quote in [09:00Z, 09:11Z); "
                   f"dropped {m['dropped']}, still subscribed without a valid quote {m['subscribed_no_valid_quote']}, new {m['new']}"),
        "quote_outcome": "ALL_RECEIVED" if n and got == n else "FEED_NO_VALID_QUOTE" if got == 0 else "PARTIAL",
        "failure_classes": classes,
        "observation": {"mode": "CACHE_LOOKBACK" if backfill or missing else "LIVE", "missing": missing,
                        "live_health": "MISSING (not observed at the time; quote evidence is a read-only look-back at the cache)" if "LIVE_HEALTH" in missing else "OBSERVED",
                        "baseline_taken_utc": (baseline or {}).get("taken_utc"), "health_coverage": cov},
        "denominator": {"n": n, "source": m["denominator_source"]},
        "counts": {k: m[k] for k in ("received", "subscribed_no_valid_quote", "dropped", "new")},
        "per_subscription": m["per_subscription"], "new_subscriptions": m["new_subscriptions"],
        "baseline_beijing_1659": None if baseline is None else {"health": baseline.get("health"),
                                                                "subscriptions_confirmed": (baseline.get("evidence") or {}).get("subscriptions_confirmed")},
        "health_first_seen_utc": first, "health_other_verdicts_seen": other, "health_transitions": trans,
        "review": {k: review.get(k) for k in ("window_utc", "window_rule", "subscriptions_confirmed", "first_valid_any_utc",
                                              "no_valid_quote_from_start_seconds", "no_valid_quote_still_open", "excluded_records",
                                              "records_in_window", "records_for_unconfirmed_symbols", "reconnects_after_start",
                                              "calendar_registered", "collected_at_utc")},
        "reconnect_and_subscription_events": {
            "provider_state_events": ev,
            "reconnect_events_after_start": [e for e in ev if e[0] in ("DISCONNECTED", "CONNECTING")],
            "subscription_state_updates_since_start": [u for u in review.get("subscription_state_updates", []) if u[1] and u[1] >= review["window_utc"][0]]},
        "redaction": "subscriptions are S1..Sn from the 16:59 set (new ones N1..), matched by salted keys that are not published; "
                     "no quote values, symbols, keys, account or server address",
    }


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() and p.stat().st_size else None


def main(argv: list[str]) -> int:
    d = Path(argv[0]); backfill = "--backfill" in argv
    review = _load(d / ("review_backfill.json" if backfill else "review_1711.json"))
    if review is None:
        print("RECEIPT_FAILED: no review evidence"); return 1
    baseline = _load(d / "baseline_1659.json")
    if baseline is not None and not baseline.get("evidence"):
        baseline = {**baseline, "evidence": None}
    hp = d / "health.jsonl"
    health = [json.loads(l) for l in hp.read_text(encoding="utf-8").splitlines() if l.strip()] if hp.exists() else []
    r = build(baseline=baseline, review=review, health=health, backfill=backfill)
    (d / "RECEIPT_redacted.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(r["result"], r["quote_outcome"], r["failure_classes"], r["observation"]["mode"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
