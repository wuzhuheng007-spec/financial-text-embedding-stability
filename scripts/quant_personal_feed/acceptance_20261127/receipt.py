"""Builds the redacted acceptance receipt from the observation files. No symbol, price or server address."""
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
health = [json.loads(l) for l in (d / "health.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
base = json.loads((d / "baseline_1659.json").read_text(encoding="utf-8"))
rev = json.loads((d / "review_1711.json").read_text(encoding="utf-8"))
first = {}
trans = []
prev = None
for h in health:
    b = h.get("business")
    first.setdefault(b, h["t"])
    if b != prev:
        trans.append({"at_utc": h["t"], "business": b, "ok": h.get("ok"), "state": h.get("state"), "grace_left_seconds": h.get("grace_left_seconds"), "down_seconds": h.get("down_seconds")})
        prev = b
ev = rev.get("state_events", [])
reconnect = [e for e in ev if e[0] in ("DISCONNECTED", "CONNECTING")]
n, got = rev["subscriptions_confirmed"], rev["subscriptions_with_valid_premarket_quote"]
receipt = {
    "schema": "feed_first_due_period_receipt_v1", "feed_image": "quant-personal-app:feed-35a2471", "process": "existing feed container, no restart, no schedule change",
    "due_period": "2026-11-27 pre-market (XNYS EARLY_CLOSE day), due from 2026-11-27T09:00:00Z (Beijing 17:00)",
    "baseline_beijing_1659": {"health": base.get("health"), "subscriptions_confirmed": base["evidence"]["subscriptions_confirmed"],
                              "state_events_last": base["evidence"]["state_events"][-3:]},
    "health_samples": len(health), "health_first_seen_utc": {k: first.get(k) for k in ("WAITING_FIRST_PUSH", "OK", "NO_FIRST_PUSH")},
    "health_other_verdicts_seen": sorted(k for k in first if k not in ("WAITING_FIRST_PUSH", "OK", "NO_FIRST_PUSH")),
    "health_transitions": trans,
    "review_beijing_1711": {k: rev[k] for k in ("window_utc", "window_rule", "subscriptions_confirmed", "subscriptions_with_valid_premarket_quote", "labels_with_valid_quote",
                                                 "labels_without_valid_quote", "first_valid_any_utc", "no_valid_quote_from_start_seconds", "no_valid_quote_still_open",
                                                 "excluded_records", "records_in_window", "reconnects_after_start", "calendar_registered")},
    "per_subscription_first_valid": {k: v["first_valid"] for k, v in rev["per_subscription"].items()},
    "per_subscription_excluded": {k: v["excluded"] for k, v in rev["per_subscription"].items()},
    "reconnect_and_subscription_events": {"provider_state_events": ev, "reconnect_events_after_start": reconnect,
                                          "subscription_state_updates_since_start": [u for u in rev.get("subscription_state_updates", []) if u[1] and u[1] >= rev["window_utc"][0]]},
    "result": f"{got}/{n} confirmed subscriptions received a valid pre-market quote in [09:00Z, 09:11Z)",
    "result_code": rev["result"],
    "redaction": "subscriptions are S1..Sn (sorted, mapping not published); no quote values, symbols, account or server address",
}
(d / "RECEIPT_redacted.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
print(receipt["result"], receipt["result_code"], receipt["health_first_seen_utc"])
