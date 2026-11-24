"""Export unadjusted regular-session daily bars from the moomoo Web API (quote:read) to CSV, for the portal's
one-year chart. Read-only towards the provider and towards the stored authorization:

- the access token is READ from the existing store and used as it is. This program never refreshes it and never
  writes the store (a second refresher next to the feed could spend the refresh token). If the token is close to
  its expiry the program stops and leaves the refresh to the feed.
- requests are date-segmented; a response that reports more pages is not accepted, its window is split instead.
- raw responses are kept privately next to the CSV files; the receipt holds no credential.

CSV columns: symbol,local_date,frequency,autype,open,high,low,close,research_eligible (local_date = New York date).
"""
import argparse
import csv
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
ENDPOINT = "https://webapi.moomoo.com/api/v1.0/quote"
STOP_HTTP, STOP_RET = (401, 403, 429, 439), (-9, -11, -12)
MIN_TOKEN_SECONDS = 900


class Stop(Exception):
    pass


def completed(day: date, now_ny: datetime) -> bool:
    """A weekday session that is over: an earlier date, or today 45 minutes after the regular close."""
    if day.weekday() >= 5:
        return False
    return day < now_ny.date() or (day == now_ny.date() and (now_ny.hour, now_ny.minute) >= (16, 45))


def windows(start: date, end: date, days: int):
    a = start
    while a <= end:
        b = min(end, a + timedelta(days=days - 1))
        yield a, b
        a = b + timedelta(days=1)


def fetch(symbol: str, a: date, b: date, num: int, token: str, raw_dir: Path, log: list) -> dict:
    url = f"{ENDPOINT}/US.{symbol}/history-kline?" + urlencode(
        {"start": a.isoformat(), "end": b.isoformat(), "ktype": 2, "autype": 0, "num": num, "extended_time": 0})
    row = {"symbol": symbol, "start": a.isoformat(), "end": b.isoformat(), "requested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "http_status": None, "ret_code": None, "items": None, "has_more": None, "response_sha256": None, "error_class": None}
    body = b""
    try:
        with urlopen(Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}), timeout=20) as r:
            row["http_status"], body = r.status, r.read(4_000_000)
    except HTTPError as ex:
        row["http_status"], row["error_class"] = ex.code, "HTTP"
        body = ex.read(4000) if ex.fp else b""
    except (URLError, TimeoutError, OSError) as ex:
        row["error_class"] = type(ex).__name__
    row["response_sha256"] = hashlib.sha256(body).hexdigest() if body else None
    data = None
    if body:
        try:
            data = json.loads(body)
        except ValueError:
            row["error_class"] = row["error_class"] or "NOT_JSON"
    if isinstance(data, dict):
        row["ret_code"] = data.get("ret_code")
        row["provider_error_code"] = (data.get("error") or {}).get("code") if isinstance(data.get("error"), dict) else None
        d = data.get("data") or {}
        if isinstance(d.get("kline_list"), list):
            row["items"] = len(d["kline_list"])
        row["has_more"] = bool(((d.get("pagination") or data.get("pagination") or {})).get("has_more"))
        if row["http_status"] == 200 and row["ret_code"] == 0:
            (raw_dir / f"US.{symbol}_{a.isoformat()}_{b.isoformat()}.json").write_bytes(body)
    log.append(row)
    if row["http_status"] in STOP_HTTP or row["ret_code"] in STOP_RET:
        raise Stop("PROVIDER_AUTHORIZATION_SECURITY_OR_RATE_STOP")
    if row["http_status"] != 200 or row["ret_code"] != 0 or row["items"] is None:
        raise Stop("PROVIDER_OR_TRANSPORT_FAILURE_STOP")
    return data


def bars_of(data: dict, symbol: str, problems: list) -> list[dict]:
    out = []
    for k in data["data"]["kline_list"]:
        try:
            d = datetime.strptime(str(k["date"]), "%Y%m%d").date()
            o, h, l, c = (float(k[x]) for x in ("open", "high", "low", "close"))
        except (KeyError, ValueError, TypeError):
            problems.append({"symbol": symbol, "problem": "UNPARSEABLE_BAR"}); continue
        if not all(math.isfinite(p) and p > 0 for p in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
            problems.append({"symbol": symbol, "date": d.isoformat(), "problem": "INVALID_OHLC"}); continue
        if k.get("time_key") is not None:
            try:
                if datetime.fromtimestamp(int(k["time_key"]) / 1000, NY).date() != d:
                    problems.append({"symbol": symbol, "date": d.isoformat(), "problem": "TIME_KEY_NOT_ON_THE_NEW_YORK_DATE"})
            except (ValueError, TypeError, OverflowError):
                problems.append({"symbol": symbol, "date": d.isoformat(), "problem": "BAD_TIME_KEY"})
        out.append({"date": d, "open": o, "high": h, "low": l, "close": c})
    return out


def run(out: Path, symbols: list[str], start: date, end: date, token: str, *, num: int, step_days: int, pause: float, max_requests: int) -> dict:
    raw_dir = out / "raw"; raw_dir.mkdir(parents=True, exist_ok=False)
    now_ny = datetime.now(NY)
    log, problems, cover, stop = [], [], {}, None
    series = {}
    try:
        for sym in symbols:
            got = {}
            todo = list(windows(start, end, step_days))
            while todo:
                a, b = todo.pop(0)
                if len(log) >= max_requests:
                    raise Stop("REQUEST_CEILING")
                if log:
                    time.sleep(pause)
                data = fetch(sym, a, b, num, token, raw_dir, log)
                if log[-1]["has_more"]:                      # more pages than one call returns: narrower windows, nothing is taken from this answer
                    if a == b:
                        raise Stop("HAS_MORE_ON_ONE_DAY")
                    mid = a + (b - a) // 2
                    todo[:0] = [(a, mid), (mid + timedelta(days=1), b)]
                    continue
                for bar in bars_of(data, sym, problems):
                    if not a <= bar["date"] <= b:
                        problems.append({"symbol": sym, "date": bar["date"].isoformat(), "problem": "OUTSIDE_THE_REQUESTED_WINDOW"}); continue
                    if bar["date"] in got and got[bar["date"]] != bar:
                        problems.append({"symbol": sym, "date": bar["date"].isoformat(), "problem": "DUPLICATE_DATE_WITH_DIFFERENT_VALUES"}); continue
                    got[bar["date"]] = bar
            series[sym] = got
    except Stop as ex:
        stop = str(ex)
    for sym, got in series.items():
        path = out / f"US.{sym}.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["symbol", "local_date", "frequency", "autype", "open", "high", "low", "close", "research_eligible"])
            for d in sorted(got):
                b = got[d]
                w.writerow(["US." + sym, d.isoformat(), "DAY", 0, b["open"], b["high"], b["low"], b["close"], 1 if completed(d, now_ny) else 0])
        ok = sorted(d for d in got if completed(d, now_ny))
        cover[sym] = {"rows": len(got), "eligible_rows": len(ok), "first_date": ok[0].isoformat() if ok else None, "last_date": ok[-1].isoformat() if ok else None,
                      "not_eligible": [d.isoformat() for d in sorted(got) if not completed(d, now_ny)], "csv": path.name,
                      "csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    union = sorted({d for got in series.values() for d in got if completed(d, now_ny)})
    for sym, got in series.items():
        cover[sym]["dates_other_symbols_have_and_this_one_lacks"] = [d.isoformat() for d in union if d not in got]
    weekdays = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return {"source": "moomoo Web API history-kline, ktype=2 (day), autype=0, extended_time=0", "ran_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "new_york_now": now_ny.isoformat(timespec="minutes"), "requested": {"symbols": symbols, "start": start.isoformat(), "end": end.isoformat(), "num": num, "window_days": step_days},
            "stop_reason": stop, "requests": len(log), "request_log": log, "coverage": cover, "problems": problems,
            "weekdays_without_a_bar_in_any_symbol": [d.isoformat() for d in weekdays if d.weekday() < 5 and completed(d, now_ny) and d not in set(union)],
            "token": "read from the existing store, not refreshed, not written"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--num", type=int, default=200)
    p.add_argument("--window-days", type=int, default=92)
    p.add_argument("--pause", type=float, default=1.5)
    p.add_argument("--max-requests", type=int, default=60)
    p.add_argument("symbols", nargs="+")
    a = p.parse_args()
    from scripts.market_observer_v1.moomoo_webapi_oauth import token_store
    from scripts.news_sentiment_factor_v44.price_pilot import TOKEN
    tok = token_store(TOKEN).load()                              # load only: access_token() could refresh and rotate the stored token
    left = float(tok.get("access_expires_epoch", 0)) - time.time()
    if left < MIN_TOKEN_SECONDS:
        print(json.dumps({"status": "TOKEN_NEAR_EXPIRY_LEFT_TO_THE_FEED", "seconds_left": int(left), "requests": 0})); raise SystemExit(3)
    res = run(a.out, [s.upper() for s in a.symbols], a.start, a.end, str(tok["access_token"]), num=a.num, step_days=a.window_days, pause=a.pause, max_requests=a.max_requests)
    res["token_seconds_left_at_start"] = int(left)
    (a.out / "export_receipt.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "request_log"}, indent=1, ensure_ascii=False))
    raise SystemExit(0 if res["stop_reason"] is None else 2)


if __name__ == "__main__":
    main()
