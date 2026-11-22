"""X providers: normalized post fields, query construction, local JSONL/CSV import, and the TwitterAPI.io date
search collector (time slices, cursor pagination, budget reservation, page cache, checkpoints, gap marking).

No paid request can leave this module unless the caller hands in a real transport, and the CLI only builds one when
`config.acquisition_gate` passes. The default transport refuses.

Provider facts used here were read from the provider's own documentation on 2026-11-22 (endpoint, `X-API-Key`,
`query` up to 512 characters, `queryType`, `cursor`, time operators `since_time:` / `until_time:` in Unix seconds
inside the query, up to 20 posts per page, `tweets` / `has_next_page` / `next_cursor`). Whether the time operators
are inclusive is not documented, so every returned post is filtered again by its own timestamp against [start, end).
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .cost import BudgetExhausted
from .ledger import LimitReached, Paused

ENDPOINT = "https://api.twitterapi.io/twitter/tweet/advanced_search"
RETRO, LIVE, BATCH = "RETROSPECTIVE_VISIBLE_NOW", "LIVE", "BATCH_AFTER_CLOSE"
_CJK = re.compile(r"[㐀-鿿]")
_URL = re.compile(r"https?://\S+")
_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text) -> str:
    s = str(text)
    for v in _SECRETS:
        s = s.replace(v, "[REDACTED]")
    return re.sub(r"(?i)(x-api-key|authorization|cookie)\s*[:=]\s*\S+", r"\1: [REDACTED]", s)


class ProviderHTTPError(Exception):
    def __init__(self, status: int | None, kind: str):
        super().__init__(kind)
        self.status, self.kind = status, kind


class PaidRequestBlocked(RuntimeError):
    """Raised by the default transport: a dry run never sends a request."""


class RefusingTransport:
    calls = 0

    def get(self, params: dict) -> dict:
        raise PaidRequestBlocked("dry run: no request is sent")


class UrllibTransport:
    """The only code path that reaches the provider. Built by the CLI after the gate passes; TLS verification is
    the interpreter default and is never switched off. One attempt per call: no retry loop, a 429 stops the run."""

    def __init__(self, api_key: str, timeout: float = 30.0):
        register_secret(api_key)
        self._key, self.timeout, self.calls = api_key, timeout, 0

    def get(self, params: dict) -> dict:
        self.calls += 1
        req = urllib.request.Request(ENDPOINT + "?" + urllib.parse.urlencode(params), headers={"X-API-Key": self._key, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - fixed https endpoint
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderHTTPError(e.code, "RATE_LIMITED" if e.code == 429 else "HTTP_503_NOT_CHARGED" if e.code == 503 else f"HTTP_{e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderHTTPError(None, "NETWORK") from None
        except ValueError:
            raise ProviderHTTPError(None, "BAD_JSON") from None


# ---------- normalized fields ----------
def keyword_lang(keyword: str) -> str:
    return "zh" if _CJK.search(keyword) else "en"


def text_hash(text: str) -> str:
    return store.sha(" ".join(text.split()).lower())


def _kind(is_retweet: bool, is_quote: bool, is_reply: bool) -> str:
    return "retweet" if is_retweet else "quote" if is_quote else "reply" if is_reply else "original"


def _post(*, provider, post_id, author_id, created_at, text, lang, url, kind, parent_available, now, batch, mode, engagement=None) -> dict:
    author_text = "" if kind == "retweet" else text
    return {"provider": provider, "post_id": str(post_id), "author_id": str(author_id), "created_at_utc": store.iso(created_at),
            "first_seen_at_utc": now, "fetched_at_utc": now, "ingest_mode": mode, "source_url": url, "text": text,
            "author_text": author_text, "lang": (lang or None), "text_hash": text_hash(author_text or text),
            "edit_status": "VERSION_UNKNOWN", "kind": kind, "parent_available": parent_available, "raw_batch": batch,
            "engagement_snapshot": ({"snapshot_at_utc": now, "note": "counts at fetch time, never a historical value", **engagement} if engagement else None)}


def normalize_twitterapi_io(t: dict, *, now: str, batch: str, mode: str) -> dict:
    """Raises ValueError/KeyError on a record without id, author id or a parseable timestamp."""
    created = datetime.strptime(t["createdAt"], "%a %b %d %H:%M:%S %z %Y")
    author = t.get("author") or {}
    if not t.get("id") or not author.get("id"):
        raise ValueError("missing id")
    is_rt = bool(t.get("isRetweet") or t.get("retweeted_tweet"))
    is_reply = bool(t.get("isReply") or t.get("inReplyToId"))
    eng = {k: t[k] for k in ("retweetCount", "replyCount", "likeCount", "quoteCount", "viewCount") if isinstance(t.get(k), int)}
    return _post(provider="twitterapi_io", post_id=t["id"], author_id=author["id"], created_at=created, text=t.get("text") or "",
                 lang=t.get("lang"), url=t.get("url"), kind=_kind(is_rt, bool(t.get("isQuote") or t.get("quoted_tweet")), is_reply),
                 parent_available=(False if is_reply else None), now=now, batch=batch, mode=mode, engagement=eng)


# ---------- local import ----------
_TRUE = {"1", "true", "yes", "y", "t"}


def _b(v) -> bool:
    return v is True or str(v).strip().lower() in _TRUE


def import_local(db, path: str | Path, *, provider: str = "local_import", now: str | None = None) -> dict:
    """JSONL or CSV with post_id, author_id, created_at_utc, text (optional: lang, source_url, is_retweet, is_quote,
    is_reply, parent_text). Imported rows are RETROSPECTIVE_VISIBLE_NOW: first_seen_at_utc is the import time, whatever
    the file claims; a first_seen column in the file is counted and ignored."""
    path, now = Path(path), now or store.utcnow()
    raw = path.read_bytes()
    batch = f"import:{store.hashlib.sha256(raw).hexdigest()[:16]}"
    text = raw.decode("utf-8-sig")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()] if path.suffix.lower() in (".jsonl", ".ndjson") else list(csv.DictReader(text.splitlines()))
    out = {"batch": batch, "rows": len(rows), "inserted": 0, "duplicates": 0, "rejected": {}, "first_seen_claims_ignored": 0, "ingest_mode": RETRO}
    for r in rows:
        try:
            if not (str(r.get("post_id") or "").strip() and str(r.get("author_id") or "").strip()):
                raise ValueError("MISSING_ID")
            if r.get("text") is None:
                raise ValueError("MISSING_TEXT")
            try:
                created = store.parse(str(r["created_at_utc"]))
            except Exception:  # noqa: BLE001
                raise ValueError("BAD_CREATED_AT") from None
            if "+" not in str(r["created_at_utc"]) and not str(r["created_at_utc"]).endswith("Z"):
                raise ValueError("CREATED_AT_WITHOUT_UTC_OFFSET")
        except ValueError as e:
            out["rejected"][str(e)] = out["rejected"].get(str(e), 0) + 1
            continue
        if r.get("first_seen_at_utc"):
            out["first_seen_claims_ignored"] += 1
        kind = _kind(_b(r.get("is_retweet")), _b(r.get("is_quote")), _b(r.get("is_reply")))
        p = _post(provider=provider, post_id=str(r["post_id"]).strip(), author_id=str(r["author_id"]).strip(), created_at=created,
                  text=str(r["text"]), lang=r.get("lang"), url=r.get("source_url"), kind=kind,
                  parent_available=(bool(r.get("parent_text")) if kind == "reply" else None), now=now, batch=batch, mode=RETRO)
        if store.put_post(db, p):
            out["inserted"] += 1
        else:
            out["duplicates"] += 1
    db.commit()
    return out


def declare_import_coverage(db, *, topic_id: str, lang: str, vocab_version: str, start_utc: str, end_utc: str, status: str, batch: str, provider: str = "local_import") -> str:
    """An import cannot prove what its collector did. The declared status is stored as a declaration
    (COMPLETE_DECLARED / PLANNED_SAMPLE / PARTIAL), never as COMPLETE."""
    if status not in ("COMPLETE_DECLARED", "PLANNED_SAMPLE", "PARTIAL"):
        raise ValueError("status must be COMPLETE_DECLARED, PLANNED_SAMPLE or PARTIAL")
    qh = store.sha(f"{provider}|{vocab_version}|{topic_id}|{lang}|IMPORT")
    db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, provider, topic_id, lang, vocab_version, "(imported file; query unknown)"))
    run_id = store.sha(f"{qh}|{start_utc}|{end_utc}|{batch}")[:24]
    store.upsert_run(db, {"run_id": run_id, "provider": provider, "query_hash": qh, "topic_id": topic_id, "lang": lang,
                          "window_start_utc": start_utc, "window_end_utc": end_utc, "status": status, "stop_reason": "DECLARED_BY_IMPORT",
                          "billing_source": "NOT_APPLICABLE", "started_at_utc": store.utcnow(), "updated_at_utc": store.utcnow()})
    db.commit()
    return run_id


# ---------- queries ----------
TIME_SUFFIX_RESERVE = len(" since_time:9999999999 until_time:9999999999")


def term(keyword: str) -> str:
    k = keyword.strip()
    return f'"{k}"' if re.search(r"[\s\-&]", k) else k


def build_queries(topic: dict, lang: str, *, max_chars: int = 512) -> list[str]:
    """OR groups of the topic's keywords in one language, packed greedily under the provider's query limit (room
    for the time operators reserved). Keyword order is the configured order, so the split is deterministic."""
    kws = [k for k in topic["keywords"] if keyword_lang(k) == lang and k not in set(topic.get("disabled_keywords") or [])]
    tail = f" lang:{lang}"
    room = max_chars - TIME_SUFFIX_RESERVE - len(tail) - 2
    out, cur = [], []
    for k in kws:
        t = term(k)
        if len(t) > room:
            raise ValueError(f"keyword longer than the query limit: {k[:20]}")
        if cur and len(" OR ".join(cur + [t])) > room:
            out.append(cur); cur = []
        cur.append(t)
    if cur:
        out.append(cur)
    return [f"({' OR '.join(g)}){tail}" for g in out]


def register_queries(db, cfg: dict, *, provider: str | None = None) -> list[dict]:
    provider = provider or cfg["provider_candidate"]
    limit = ((cfg["acquisition"].get("provider_documented") or {}).get(provider) or {}).get("max_query_chars") or 512
    out = []
    for t in cfg["topics"]:
        for lang in cfg["languages"]:
            for q in build_queries(t, lang, max_chars=limit):
                qh = store.sha(f"{provider}|{cfg['topic_vocabulary_version']}|{q}")
                db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (qh, provider, t["id"], lang, cfg["topic_vocabulary_version"], q))
                out.append({"query_hash": qh, "topic_id": t["id"], "lang": lang, "query": q})
    db.commit()
    return out


def slices(start: datetime, end: datetime, step_seconds: int) -> list[tuple[int, int]]:
    s, e = int(start.timestamp()), int(end.timestamp())
    if e <= s:
        raise ValueError("empty window")
    return [(a, min(a + step_seconds, e)) for a in range(s, e, step_seconds)]


# ---------- collector ----------
class StopAll(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Collector:
    def __init__(self, db, cfg: dict, *, budget, transport=None, mode: str = RETRO, clock=None, free_gib=None):
        acq = cfg["acquisition"]
        self.db, self.cfg, self.budget, self.mode = db, cfg, budget, mode
        self.transport = transport or RefusingTransport()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_pages = acq["proposed_max_pages_per_slice"]
        self.min_slice = acq["proposed_min_slice_seconds"]
        self.max_dup = acq["proposed_max_duplicate_share_per_page"]
        self.provider = "twitterapi_io"
        self.parse_failures, self.protocol_anomalies, self.last = 0, 0, None
        self.page_max = ((acq.get("provider_documented") or {}).get("twitterapi_io") or {}).get("max_posts_per_page") or 20
        self.min_free_gib = acq["proposed_min_free_disk_gib"]
        self.free_gib = free_gib or (lambda: shutil.disk_usage(Path(db.execute("PRAGMA database_list").fetchone()[2] or ".").parent).free / 2**30)

    def _run_id(self, qh: str, s: int, e: int) -> str:
        return store.sha(f"{self.provider}|{qh}|{s}|{e}")[:24]

    def collect(self, q: dict, start: datetime, end: datetime) -> list[dict]:
        """Top-level slices of [start, end) for one registered query. Returns one summary per leaf run. Stops the
        whole call (remaining slices stay NOT_RUN) on budget exhaustion, a 429 or a blocked dry run."""
        out = []
        try:
            for s, e in slices(start, end, self.cfg["acquisition"]["proposed_first_slice_seconds"]):
                if self.free_gib() < self.min_free_gib:     # pause new collection; stored evidence is never deleted to make room
                    raise StopAll("DISK_LOW_COLLECTION_PAUSED")
                out.extend(self._slice(q, s, e, None))
        except StopAll as stop:
            out.append({"stopped": stop.reason})
        return out

    def collect_pages(self, q: dict, s: int, e: int, upto_pages: int) -> dict:
        """Planned sample: read one window up to a frozen number of pages, resuming where the last call stopped.
        Never splits or widens. The result is PLANNED_SAMPLE (page limit reached, more may exist) or COMPLETE (the
        provider reported the end). Raises StopAll when the whole job must stop."""
        return self._slice(q, s, e, None, planned_pages=upto_pages)[0]

    def _slice(self, q: dict, s: int, e: int, parent: str | None, planned_pages: int | None = None) -> list[dict]:
        run_id = self._run_id(q["query_hash"], s, e)
        prev = self.db.execute("SELECT * FROM x_query_runs WHERE run_id=?", (run_id,)).fetchone()
        if planned_pages is not None and prev and prev["status"] == "PLANNED_SAMPLE" and prev["pages"] >= planned_pages:
            return [{"run_id": run_id, "status": "PLANNED_SAMPLE", "stop_reason": prev["stop_reason"], "from_checkpoint": True, "requests": 0, "network_requests": 0,
                     "cache_hits": 0, "pages": prev["pages"], "returned": prev["returned"], "in_window": prev["in_window"], "out_of_window": prev["out_of_window"],
                     "duplicates": prev["duplicates"], "new_posts": prev["new_posts"], "parse_failures": 0, "cost_estimate_usd": prev["cost_estimate_usd"]}]
        if prev and prev["status"] == "COMPLETE":
            return [{"run_id": run_id, "status": "COMPLETE", "stop_reason": prev["stop_reason"], "from_checkpoint": True, "requests": 0, "network_requests": 0, "cache_hits": 0,
                     "pages": prev["pages"], "returned": prev["returned"], "in_window": prev["in_window"], "out_of_window": prev["out_of_window"],
                     "duplicates": prev["duplicates"], "new_posts": prev["new_posts"], "parse_failures": 0, "cost_estimate_usd": prev["cost_estimate_usd"]}]
        if prev and prev["status"] == "SPLIT":
            mid = (s + e) // 2
            return self._slice(q, s, mid, run_id) + self._slice(q, mid, e, run_id)
        ws, we = store.iso(datetime.fromtimestamp(s, timezone.utc)), store.iso(datetime.fromtimestamp(e, timezone.utc))
        run = {"run_id": run_id, "provider": self.provider, "query_hash": q["query_hash"], "topic_id": q["topic_id"], "lang": q["lang"],
               "window_start_utc": ws, "window_end_utc": we, "parent_run_id": parent, "status": "IN_PROGRESS", "stop_reason": None,
               "cursor": "", "pages": 0, "requests": 0, "returned": 0, "in_window": 0, "out_of_window": 0, "duplicates": 0, "new_posts": 0,
               "earliest_utc": None, "latest_utc": None, "cost_estimate_usd": "0", "error_class": None,
               "started_at_utc": store.iso(self.clock()), "updated_at_utc": store.iso(self.clock())}
        if prev:                                           # resume an interrupted or failed slice from its checkpoint
            for k in ("cursor", "pages", "requests", "returned", "in_window", "out_of_window", "duplicates", "new_posts", "earliest_utc", "latest_utc", "cost_estimate_usd", "started_at_utc"):
                run[k] = prev[k]
        seen_ids = {r["post_id"] for r in self.db.execute("SELECT post_id FROM x_run_posts WHERE run_id=?", (run_id,))}
        seen_cursors, cursor, prev_oldest, needs_split, stop, over_page, protocol = set(), run["cursor"] or "", None, None, None, False, None
        net, hits, fails = 0, 0, 0
        query = f"{q['query']} since_time:{s} until_time:{e}"
        while True:
            if cursor in seen_cursors:
                needs_split = "DUPLICATE_CURSOR"; break
            if planned_pages is not None and run["pages"] >= planned_pages:
                run["status"], run["stop_reason"] = "PLANNED_SAMPLE", "PLANNED_PAGE_LIMIT"; break
            if run["pages"] >= self.max_pages:
                needs_split = "PAGE_CAP"; break
            seen_cursors.add(cursor)
            cached = self.db.execute("SELECT response_json, fetched_at_utc FROM x_page_cache WHERE provider=? AND query_hash=? AND window_start_utc=? AND window_end_utc=? AND cursor=?",
                                     (self.provider, q["query_hash"], ws, we, cursor)).fetchone()
            if cached:                                       # a stored page costs nothing and keeps the time it was really received
                page, now = json.loads(cached["response_json"]), cached["fetched_at_utc"]
                hits += 1
            else:
                if isinstance(self.transport, RefusingTransport):
                    stop = "DRY_RUN_NO_REQUEST"; break          # nothing is reserved for a request that cannot be sent
                led = hasattr(self.budget, "context")
                if led:
                    retry = bool(prev and prev["cursor"] == cursor and prev["status"] == "PARTIAL")
                    self.budget.context = {"run_id": run_id, "ws": ws, "we": we, "cursor": cursor, "attempt": 2 if retry else 1}
                try:
                    self.budget.reserve_next_page(self.clock())
                except BudgetExhausted as ex:
                    stop = f"BUDGET_TRUNCATED:{ex.limit_kind}"; break
                except LimitReached as ex:
                    stop = f"LIMIT_REACHED:{ex.limit_kind}"; break
                except Paused as ex:
                    stop = f"PAUSED:{ex}"; break
                try:
                    page = self.transport.get({"query": query, "queryType": "Latest", "cursor": cursor})
                except PaidRequestBlocked:
                    stop = "DRY_RUN_NO_REQUEST"; break
                except ProviderHTTPError as ex:
                    run["error_class"] = ex.kind; net += 1
                    if led:                                  # keep the reservation unless the provider documents no charge
                        if ex.kind == "HTTP_503_NOT_CHARGED":
                            self.budget.not_charged("provider documents HTTP 503 as not charged")
                        else:
                            self.budget.unknown(ex.kind)
                    stop = "RATE_LIMITED" if ex.kind == "RATE_LIMITED" else f"ERROR:{ex.kind}"; break
                now = store.iso(self.clock())                # the moment the response was actually received
                net += 1
                tweets = page.get("tweets") if isinstance(page, dict) else None
                if not isinstance(tweets, list):             # how many posts came back is not knowable: the reservation stays
                    run["error_class"] = "BAD_SHAPE"
                    if led:
                        self.budget.unknown("PROTOCOL:TWEETS_NOT_A_LIST")
                    protocol = "PROTOCOL:TWEETS_NOT_A_LIST"; break
                ids = {str(t.get("id")) for t in tweets if isinstance(t, dict) and t.get("id")}
                cost = self.budget.charge(len(tweets), self.clock(), len(ids)) if led else self.budget.charge(len(tweets), self.clock())
                if len(tweets) > self.page_max:
                    over_page = True
                run["requests"] += 1
                run["cost_estimate_usd"] = str(store_decimal(run["cost_estimate_usd"]) + cost)
                body = json.dumps(page, sort_keys=True, ensure_ascii=False)
                self.db.execute("INSERT OR REPLACE INTO x_page_cache VALUES(?,?,?,?,?,?,?,?)", (self.provider, q["query_hash"], ws, we, cursor, store.sha(body), body, now))
            bad = page_schema_error(page)
            if bad:                                          # paid for and kept for inspection, but not read as a result and never an end of results
                run["error_class"] = bad
                run["updated_at_utc"] = now
                protocol = f"PROTOCOL:{bad}"; break
            tweets = page["tweets"]
            run["pages"] += 1; run["returned"] += len(tweets)
            times, dup_here = [], 0
            for t in tweets:
                try:
                    p = normalize_twitterapi_io(t, now=now, batch=f"run:{run_id}", mode=self.mode)
                except (KeyError, ValueError, TypeError, AttributeError):
                    self.parse_failures += 1; fails += 1; run["out_of_window"] += 1; continue
                ts = int(store.parse(p["created_at_utc"]).timestamp()); times.append(ts)
                if p["post_id"] in seen_ids:
                    dup_here += 1; continue
                seen_ids.add(p["post_id"])
                if not (s <= ts < e):                       # the operators' edges are undocumented: filter again, half-open
                    run["out_of_window"] += 1; continue
                run["in_window"] += 1
                if store.put_post(self.db, p):
                    run["new_posts"] += 1
                self.db.execute("INSERT OR IGNORE INTO x_run_posts VALUES(?,?,?)", (run_id, self.provider, p["post_id"]))
                run["earliest_utc"] = min(run["earliest_utc"] or p["created_at_utc"], p["created_at_utc"])
                run["latest_utc"] = max(run["latest_utc"] or p["created_at_utc"], p["created_at_utc"])
            run["duplicates"] += dup_here
            has_next, nxt = page["has_next_page"], page["next_cursor"]
            if has_next and not nxt:                         # "more results" without a way to ask for them
                run["updated_at_utc"] = now
                store.upsert_run(self.db, run); self.db.commit()
                protocol = "PROTOCOL:NEXT_PAGE_WITHOUT_CURSOR"; break
            if has_next and nxt == cursor and planned_pages is not None:
                run["updated_at_utc"] = now
                store.upsert_run(self.db, run); self.db.commit()
                protocol = "PROTOCOL:CURSOR_NOT_ADVANCING"; break
            cursor = nxt if has_next else ""
            run["cursor"], run["updated_at_utc"] = cursor, now
            store.upsert_run(self.db, run); self.db.commit()   # checkpoint after every page
            if over_page:
                stop = "PAUSED:PAGE_OVER_DOCUMENTED_MAX"; break
            if times and prev_oldest is not None and max(times) > prev_oldest:
                needs_split = "ORDER_ANOMALY"; break
            if tweets and dup_here / len(tweets) > self.max_dup:
                needs_split = "EXCESS_DUPLICATES"; break
            if times:
                prev_oldest = min(times)
            if has_next is False:                            # only an explicit boolean false ends a window
                run["status"], run["stop_reason"] = "COMPLETE", ("END_OF_RESULTS" if run["returned"] else "END_OF_RESULTS_EMPTY"); break
        extra = {"network_requests": net, "cache_hits": hits, "parse_failures": fails}
        if stop:
            limit = stop.startswith("LIMIT_REACHED") or stop.startswith("PAUSED:")
            if stop == "DRY_RUN_NO_REQUEST" or (limit and run["pages"] == 0):
                run["status"] = "NOT_RUN"
            elif limit and planned_pages is not None and not over_page:
                run["status"] = "PLANNED_SAMPLE"             # what was read so far stands; the queue continues after the cap resets
            else:
                run["status"] = "LIMIT_REACHED" if limit else "PARTIAL"
            run["stop_reason"] = stop
            store.upsert_run(self.db, run); self.db.commit()
            self.last = {**{k: run[k] for k in SUMMARY}, **extra}
            raise StopAll(stop)
        if protocol:                                         # this unit stops here; it is never COMPLETE and is not retried blindly
            run["status"], run["stop_reason"] = "PARTIAL", protocol
            self.protocol_anomalies += 1
            store.upsert_run(self.db, run); self.db.commit()
            self.last = {**{k: run[k] for k in SUMMARY}, **extra}
            if self.protocol_anomalies >= 3:
                raise StopAll("PROTOCOL_ANOMALY_REPEATED")
            return [self.last]
        if needs_split and planned_pages is not None:
            run["status"], run["stop_reason"] = "PARTIAL", needs_split      # a planned window is never re-cut: it is reported as it is
        elif needs_split:
            if e - s > self.min_slice:                      # narrow the window instead of walking the oldest second back
                run["status"], run["stop_reason"] = "SPLIT", needs_split
                store.upsert_run(self.db, run); self.db.commit()
                mid = (s + e) // 2
                return self._slice(q, s, mid, run_id) + self._slice(q, mid, e, run_id)
            run["status"], run["stop_reason"] = "PARTIAL", needs_split
        store.upsert_run(self.db, run); self.db.commit()
        self.last = {**{k: run[k] for k in SUMMARY}, **extra}
        return [self.last]


SUMMARY = ("run_id", "status", "stop_reason", "pages", "requests", "returned", "in_window", "out_of_window", "duplicates", "new_posts", "cost_estimate_usd")
PROTOCOL_VALIDATOR = "x-protocol-v2-20261122"


def page_schema_error(page) -> str | None:
    """The provider documents `tweets` (array), `has_next_page` (boolean) and `next_cursor` (string) as required.
    Anything else is a protocol error: a missing field, null, the string "false" or the number 0 is never read
    as "no more pages"."""
    if not isinstance(page, dict):
        return "RESPONSE_NOT_AN_OBJECT"
    if not isinstance(page.get("tweets"), list):
        return "TWEETS_NOT_A_LIST"
    if "has_next_page" not in page:
        return "HAS_NEXT_PAGE_MISSING"
    if not isinstance(page["has_next_page"], bool):
        return "HAS_NEXT_PAGE_NOT_BOOLEAN"
    if "next_cursor" not in page:
        return "NEXT_CURSOR_MISSING"
    if not isinstance(page["next_cursor"], str):
        return "NEXT_CURSOR_NOT_STRING"
    if any(not isinstance(t, dict) for t in page["tweets"]):
        return "TWEET_NOT_AN_OBJECT"
    return None


def migrate_checkpoints(db) -> dict:
    """Re-reads every stored page with the strict validator. A run that the earlier, lenient reader marked COMPLETE
    although its last stored page does not say `has_next_page: false` (or any stored page is malformed) is demoted
    to PARTIAL so it is not skipped for ever. Nothing is deleted, no cost is touched, no request is sent.
    Idempotent; each application is logged in x_migrations."""
    db.execute("CREATE TABLE IF NOT EXISTS x_migrations(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, applied_at_utc TEXT NOT NULL, detail_json TEXT NOT NULL)")
    done = db.execute("SELECT detail_json FROM x_migrations WHERE name=? ORDER BY id DESC LIMIT 1", (PROTOCOL_VALIDATOR,)).fetchone()
    demoted, checked = [], 0
    for r in db.execute("SELECT run_id, query_hash, window_start_utc, window_end_utc, stop_reason FROM x_query_runs WHERE provider='twitterapi_io' AND status='COMPLETE'").fetchall():
        pages = db.execute("SELECT cursor, response_json FROM x_page_cache WHERE provider='twitterapi_io' AND query_hash=? AND window_start_utc=? AND window_end_utc=? ORDER BY fetched_at_utc, rowid",
                           (r["query_hash"], r["window_start_utc"], r["window_end_utc"])).fetchall()
        checked += 1
        why = None
        if not pages:
            why = "NO_STORED_PAGE"
        else:
            parsed = [json.loads(p["response_json"]) for p in pages]
            errs = [page_schema_error(x) for x in parsed]
            if any(errs):
                why = next(e for e in errs if e)
            elif not any(x["has_next_page"] is False for x in parsed):
                why = "NO_PAGE_WITH_EXPLICIT_END"
        if why:
            db.execute("UPDATE x_query_runs SET status='PARTIAL', stop_reason=? WHERE run_id=?", (f"MIGRATED_FROM_LENIENT_COMPLETE:{why}", r["run_id"]))
            demoted.append({"run_id": r["run_id"], "reason": why, "previous_stop_reason": r["stop_reason"]})
    detail = {"validator": PROTOCOL_VALIDATOR, "complete_runs_checked": checked, "demoted": demoted, "first_application": done is None}
    if done is None or demoted:
        db.execute("INSERT INTO x_migrations(name, applied_at_utc, detail_json) VALUES(?,?,?)", (PROTOCOL_VALIDATOR, store.utcnow(), json.dumps(detail, sort_keys=True)))
    db.commit()
    return detail


def store_decimal(s):
    from decimal import Decimal
    return Decimal(str(s or "0"))


def plan(cfg: dict, queries: list[dict], start: datetime, end: datetime) -> dict:
    """What a run WOULD ask for, with no request sent: slices, minimum cost if every slice is empty, and the
    cost if every day returned the proposed cap. Not a forecast of how many posts exist."""
    from decimal import Decimal
    provider = cfg["provider_candidate"]
    rate = Decimal(cfg["quoted_standard_rates_usd_per_returned_post"][provider]); minimum = Decimal(cfg["minimum_charge_usd_per_request"][provider])
    sl = slices(start, end, cfg["acquisition"]["proposed_first_slice_seconds"])
    days = Decimal(int(end.timestamp()) - int(start.timestamp())) / Decimal(86400)
    return {"provider": provider, "requests_sent": 0, "queries": len(queries), "top_level_slices": len(sl) * len(queries),
            "minimum_cost_if_every_slice_is_empty_usd": str(minimum * len(sl) * len(queries)),
            "cost_at_proposed_daily_cap_usd": str((Decimal(cfg["acquisition"]["proposed_total_returned_post_cap_per_day"]) * days * rate).quantize(Decimal("0.0001"))),
            "basis": "quoted standard rate; excludes retries, tax and plan differences; not a bill"}
