"""X stance study, collection side: frozen parameters and gates, cost arithmetic, query construction, the
paginated collector against a scripted fake provider, and local import. Everything here is synthetic and offline:
no request is sent, no real post is read, and nothing measures how complete any provider is."""
import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scripts.x_stance_v1 import config as C
from scripts.x_stance_v1 import cost, provider, store

UTC = timezone.utc
T0 = datetime(2026, 1, 5, tzinfo=UTC)


@pytest.fixture()
def cfg():
    return C.load()


@pytest.fixture()
def db(tmp_path):
    d = store.connect(tmp_path / "x.sqlite3")
    yield d
    d.close()


def tweet(i, ts, author=None, text=None, **kw):
    return {"id": str(i), "url": f"https://x.com/u/status/{i}", "text": text or f"$QQQ note {i}", "lang": "en",
            "createdAt": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"), "author": {"id": str(author or 1000 + i)}, "likeCount": 7, **kw}


class Fake:
    """Serves scripted pages keyed by (window, cursor). Records every request it receives."""

    def __init__(self, pages=None, fn=None):
        self.pages, self.fn, self.requests = pages or {}, fn, []

    def get(self, params):
        self.requests.append(dict(params))
        q = params["query"]
        s, e = int(q.split("since_time:")[1].split()[0]), int(q.split("until_time:")[1].split()[0])
        if self.fn:
            return self.fn(s, e, params["cursor"])
        return self.pages[(s, e, params["cursor"])]


def budget(total="5", daily="5", cap=10_000):
    return cost.Budget(rate="0.00015", minimum="0.00015", page_max=20, total_usd=total, daily_usd=daily, daily_post_cap=cap)


Q = {"query_hash": "qh1", "topic_id": "qqq", "lang": "en", "query": "($QQQ) lang:en"}


def reg(db, q=Q):
    db.execute("INSERT OR IGNORE INTO x_queries VALUES(?,?,?,?,?,?)", (q["query_hash"], "twitterapi_io", q["topic_id"], q["lang"], "vocab-20261122-a", q["query"]))


# ---------- parameters and gates ----------
def test_unknown_values_stay_null_and_are_listed(cfg):
    names = {u["field"] for u in C.unfrozen(cfg)}
    assert {"classifier.model_id", "classifier.immutable_revision", "market.input_version", "validation.holdout_start", "topics[memory].comparison_asset"} <= names
    assert "topics[qqq].comparison_asset" not in names and not any(n.startswith("acquisition.") for n in names)      # provider, budget and cadence are now fixed
    assert tuple(cfg["horizons_trading_sessions"]) == (1, 3, 5, 10, 20, 30) and tuple(cfg["stance_labels"]) == ("bullish", "bearish", "neutral")
    assert "proposed_total_returned_post_cap_per_day" not in cfg["acquisition"] and cfg["budget"]["daily_all_jobs_returned_post_cap"] == 5000


def test_acquisition_fails_closed_without_key_or_preflight(cfg):
    g = C.acquisition_gate(cfg, {})
    assert not g["allowed"] and g["reasons"] == ["PROVIDER_KEY_MISSING", "PREFLIGHT_NOT_PASSED"] and g["key_env_name"] == "TWITTERAPI_IO_API_KEY"
    secret = "tw-FAKE-not-a-real-key-000"
    g = C.acquisition_gate(cfg, {"TWITTERAPI_IO_API_KEY": secret})                 # a key alone does not start the scheduled jobs
    assert not g["allowed"] and g["reasons"] == ["PREFLIGHT_NOT_PASSED"] and secret not in json.dumps(g)
    assert C.acquisition_gate(cfg, {"TWITTERAPI_IO_API_KEY": secret}, for_preflight=True)["allowed"]
    assert C.acquisition_gate(cfg, {"TWITTERAPI_IO_API_KEY": secret}, preflight_passed=True)["allowed"]
    assert not C.acquisition_gate(cfg, {"TWITTERAPI_IO_API_KEY": "  "}, preflight_passed=True)["allowed"]
    assert not C.acquisition_gate(cfg, {"X_STANCE_TWITTERAPI_IO_KEY": secret}, preflight_passed=True)["allowed"]   # only the agreed variable name counts


@pytest.mark.parametrize("path,value", [("automatic_paid_fallback", True), ("acquisition.sort", "Top"), ("acquisition.historical_min_likes_filter", 10),
                                        ("aggregation.engagement_weighting", True), ("stance_labels", ["bullish", "bearish", "neutral", "mixed"]),
                                        ("horizons_trading_sessions", [1, 3, 5, 10, 20]), ("narratives.max_clusters_to_display", 6),
                                        ("authorization.auto_topup", True), ("authorization.purchase_subscription", True), ("allocation.automatic_reallocation", True),
                                        ("allocation.recent.max_returned_posts_per_day", 4000), ("budget.daily_all_jobs_cost_cap_usd", "0.50"),
                                        ("preflight.included_in_daily_and_rolling_caps", False), ("budget.rolling_all_jobs_cost_cap_usd", None)])
def test_config_rejects_what_the_contract_forbids(cfg, path, value):
    cur = cfg
    *head, last = path.split(".")
    for h in head:
        cur = cur[h]
    cur[last] = value
    with pytest.raises(C.ConfigError):
        C.validate(cfg)


@pytest.mark.parametrize("cmd", ["preflight", "daily"])
def test_cli_metered_commands_refuse_and_send_nothing_without_a_key(tmp_path, capsys, monkeypatch, cmd):
    from scripts.x_stance_v1 import cli
    monkeypatch.delenv("TWITTERAPI_IO_API_KEY", raising=False)
    monkeypatch.setattr(provider.UrllibTransport, "get", lambda *a, **k: pytest.fail("a request was attempted"))
    assert cli.main(["--db", str(tmp_path / "x.sqlite3"), cmd]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "REFUSED_FAIL_CLOSED" and out["requests_sent"] == 0 and "PROVIDER_KEY_MISSING" in out["gate"]["reasons"]
    db = store.connect(tmp_path / "x.sqlite3")
    assert db.execute("SELECT count(*) FROM x_cost_ledger").fetchone()[0] == 0     # nothing reserved, nothing spent
    db.close()


# ---------- cost ----------
def test_minimum_request_fee_for_zero_one_and_many():
    r = m = Decimal("0.00015")
    assert cost.request_cost(0, r, m) == Decimal("0.00015")
    assert cost.request_cost(1, r, m) == Decimal("0.00015")                       # the minimum is not added on top of a returned post
    assert cost.request_cost(3, r, m) == Decimal("0.00045") and cost.request_cost(20, r, m) == Decimal("0.00300")


def test_monthly_table_reproduces_the_contract_figures(cfg):
    t = {r["daily_returned_posts"]: r for r in cost.cost_table(cfg)}
    assert [t[n]["twitterapi_io_usd"] for n in (5000, 10000, 30000)] == ["22.50", "45.00", "135.00"]
    assert [t[n]["socialdata_usd"] for n in (5000, 10000, 30000)] == ["30.00", "60.00", "180.00"]
    assert [t[n]["x_official_usd"] for n in (5000, 10000, 30000)] == ["750.00", "1500.00", "4500.00"]


def test_budget_missing_fails_closed_and_boundary_is_exact():
    with pytest.raises(cost.BudgetMissing):
        cost.Budget(rate="0.00015", minimum="0.00015", page_max=20, total_usd=None, daily_usd="1", daily_post_cap=100)
    b = budget(total="0.006", daily="1")                                       # exactly two worst-case pages (2 x 20 x 0.00015)
    b.reserve_next_page(); b.charge(20); b.reserve_next_page(); b.charge(20)
    with pytest.raises(cost.BudgetExhausted) as e:
        b.reserve_next_page()
    assert e.value.limit_kind == "TOTAL_USD" and b.spent_total == Decimal("0.006")
    b = budget(cap=30); b.reserve_next_page(); b.charge(20)
    with pytest.raises(cost.BudgetExhausted) as e:                             # 20 + a possible 20 would pass the 30-post cap
        b.reserve_next_page()
    assert e.value.limit_kind == "DAILY_POST_CAP"


# ---------- queries ----------
def test_queries_cover_every_keyword_once_in_provider_syntax(cfg):
    for t in cfg["topics"]:
        seen = []
        for lang in cfg["languages"]:
            for q in provider.build_queries(t, lang):
                assert len(q) + provider.TIME_SUFFIX_RESERVE <= 512 and q.endswith(f" lang:{lang}")
                seen += [x.strip('"') for x in q[1:q.rindex(")")].split(" OR ")]
        assert sorted(seen) == sorted(t["keywords"])
    q = provider.build_queries(C.topic(cfg, "qqq"), "en")[0]
    assert q == '($QQQ OR "Nasdaq 100" OR "Nasdaq-100") lang:en'                  # phrases quoted, cashtag left as is
    assert provider.build_queries(C.topic(cfg, "qqq"), "zh") == ["(纳斯达克100 OR 纳指100) lang:zh"]


def test_long_vocabulary_is_split_not_truncated():
    t = {"id": "x", "keywords": [f"keyword number {i:03d}" for i in range(60)]}
    qs = provider.build_queries(t, "en")
    assert len(qs) > 1 and all(len(q) + provider.TIME_SUFFIX_RESERVE <= 512 for q in qs)
    assert sum(q.count('"') // 2 for q in qs) == 60 and qs == provider.build_queries(t, "en")
    with pytest.raises(ValueError):
        provider.build_queries({"id": "x", "keywords": ["k" * 600]}, "en")


# ---------- collector ----------
def test_dry_run_default_transport_sends_nothing(db, cfg):
    reg(db)
    col = provider.Collector(db, cfg, budget=budget())
    out = col.collect(Q, T0, T0 + timedelta(days=2))
    assert out[-1] == {"stopped": "DRY_RUN_NO_REQUEST"} and col.budget.requests == 0 and col.budget.spent_total == 0
    cov = store.coverage(db, "qqq", store.iso(T0), store.iso(T0 + timedelta(days=2)))
    assert cov["status"] in ("MISSING", "PARTIAL") and db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 0


def test_pagination_latest_time_operators_and_half_open_refilter(db, cfg):
    reg(db)
    s, e = int(T0.timestamp()), int((T0 + timedelta(days=1)).timestamp())
    at = lambda sec: datetime.fromtimestamp(sec, UTC)
    f = Fake({(s, e, ""): {"tweets": [tweet(5, at(e)), tweet(4, at(e - 1)), tweet(3, at(s + 50))], "has_next_page": True, "next_cursor": "c1"},
              (s, e, "c1"): {"tweets": [tweet(2, at(s)), tweet(1, at(s - 1))], "has_next_page": False, "next_cursor": ""}})
    col = provider.Collector(db, cfg, budget=budget(), transport=f)
    r = col.collect(Q, T0, T0 + timedelta(days=1))[0]
    assert r["status"] == "COMPLETE" and r["pages"] == 2 and r["returned"] == 5
    assert r["in_window"] == 3 and r["out_of_window"] == 2                       # start included, end excluded, whatever the operators do
    assert {x[0] for x in db.execute("SELECT post_id FROM x_posts")} == {"2", "3", "4"}
    assert all(p["queryType"] == "Latest" and f"since_time:{s} until_time:{e}" in p["query"] for p in f.requests)
    assert r["cost_estimate_usd"] == "0.00075" and col.budget.spent_total == Decimal("0.00075")   # 5 returned posts are billed, incl. the 2 discarded
    assert store.coverage(db, "qqq", store.iso(T0), store.iso(T0 + timedelta(days=1)))["status"] == "COMPLETE"
    n = len(f.requests)
    assert col.collect(Q, T0, T0 + timedelta(days=1))[0]["from_checkpoint"] and len(f.requests) == n   # a finished slice is not bought twice


def test_many_posts_in_one_second_are_all_kept(db, cfg):
    reg(db)
    s, e = int(T0.timestamp()), int((T0 + timedelta(days=1)).timestamp())
    same = datetime.fromtimestamp(s + 100, UTC)
    pages = {(s, e, c): {"tweets": [tweet(1000 + 20 * i + j, same) for j in range(20)], "has_next_page": i < 2, "next_cursor": (f"c{i + 1}" if i < 2 else "")}
             for i, c in enumerate(["", "c1", "c2"])}
    r = provider.Collector(db, cfg, budget=budget(), transport=Fake(pages)).collect(Q, T0, T0 + timedelta(days=1))[0]
    assert r["status"] == "COMPLETE" and db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 60


def test_repeated_cursor_narrows_the_window_then_marks_partial(db, cfg):
    reg(db)
    n = iter(range(10**6))
    def fn(s, e, cursor):                                                      # the provider keeps handing back the same cursor
        return {"tweets": [tweet(next(n), datetime.fromtimestamp(s, UTC))], "has_next_page": True, "next_cursor": "loop"}
    f = Fake(fn=fn)
    out = provider.Collector(db, cfg, budget=budget(), transport=f).collect(Q, T0, T0 + timedelta(hours=4))
    st = [r["status"] for r in db.execute("SELECT status FROM x_query_runs ORDER BY window_start_utc, window_end_utc DESC")]
    assert st.count("SPLIT") == 3 and st.count("PARTIAL") == 4 and "COMPLETE" not in st      # 4h -> 2h -> 1h (the floor), never silently "done"
    assert {r["stop_reason"] for r in out} == {"DUPLICATE_CURSOR"}
    cov = store.coverage(db, "qqq", store.iso(T0), store.iso(T0 + timedelta(hours=4)))
    assert cov["status"] == "PARTIAL" and len(cov["gaps"]) == 4


def test_out_of_order_pages_trigger_a_split(db, cfg):
    reg(db)
    s, e = int(T0.timestamp()), int((T0 + timedelta(hours=2)).timestamp())
    at = lambda sec: datetime.fromtimestamp(sec, UTC)
    def fn(a, b, cursor):
        if (a, b) == (s, e):                                                   # page 2 is NEWER than page 1's oldest post
            return {"tweets": [tweet(1, at(s + 10))], "has_next_page": True, "next_cursor": "n"} if cursor == "" else {"tweets": [tweet(2, at(s + 5000))], "has_next_page": True, "next_cursor": "m"}
        inside = [tweet(i, at(t)) for i, t in ((1, s + 10), (2, s + 5000)) if a <= t < b]
        return {"tweets": inside, "has_next_page": False, "next_cursor": ""}
    out = provider.Collector(db, cfg, budget=budget(), transport=Fake(fn=fn)).collect(Q, T0, T0 + timedelta(hours=2))
    assert [r["status"] for r in out] == ["COMPLETE", "COMPLETE"]
    assert db.execute("SELECT stop_reason FROM x_query_runs WHERE status='SPLIT'").fetchone()[0] == "ORDER_ANOMALY"
    assert db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 2


def test_budget_stop_is_partial_and_later_slices_stay_not_run(db, cfg):
    reg(db)
    def fn(s, e, cursor):
        n = int(cursor or 0)
        return {"tweets": [tweet(s + n * 20 + j, datetime.fromtimestamp(s + 1000 - n, UTC)) for j in range(20)], "has_next_page": True, "next_cursor": str(n + 1)}
    f = Fake(fn=fn)
    col = provider.Collector(db, cfg, budget=budget(total="0.009"), transport=f)          # three worst-case pages
    out = col.collect(Q, T0, T0 + timedelta(days=3))
    assert out[-1] == {"stopped": "BUDGET_TRUNCATED:TOTAL_USD"} and len(f.requests) == 3 and col.budget.spent_total <= Decimal("0.009")
    assert db.execute("SELECT status, stop_reason FROM x_query_runs").fetchall()[0][:] == ("PARTIAL", "BUDGET_TRUNCATED:TOTAL_USD")
    cov = store.coverage(db, "qqq", store.iso(T0), store.iso(T0 + timedelta(days=3)))
    assert cov["status"] == "PARTIAL" and {g["status"] for g in cov["gaps"]} == {"PARTIAL", "NOT_RUN"}


def test_429_stops_at_once_without_retry(db, cfg):
    reg(db)
    class Limited:
        n = 0
        def get(self, params):
            Limited.n += 1
            raise provider.ProviderHTTPError(429, "RATE_LIMITED")
    out = provider.Collector(db, cfg, budget=budget(), transport=Limited()).collect(Q, T0, T0 + timedelta(days=5))
    assert out == [{"stopped": "RATE_LIMITED"}] and Limited.n == 1


def test_interrupted_slice_resumes_from_its_checkpoint(db, cfg):
    reg(db)
    s, e = int(T0.timestamp()), int((T0 + timedelta(days=1)).timestamp())
    at = lambda sec: datetime.fromtimestamp(sec, UTC)
    state = {"fail": True}
    def fn(a, b, cursor):
        if cursor == "c1" and state["fail"]:
            raise provider.ProviderHTTPError(None, "NETWORK")
        return {"": {"tweets": [tweet(9, at(s + 900))], "has_next_page": True, "next_cursor": "c1"},
                "c1": {"tweets": [tweet(8, at(s + 800))], "has_next_page": False, "next_cursor": ""}}[cursor]
    f = Fake(fn=fn)
    col = provider.Collector(db, cfg, budget=budget(), transport=f)
    assert col.collect(Q, T0, T0 + timedelta(days=1))[-1] == {"stopped": "ERROR:NETWORK"}
    assert db.execute("SELECT status, cursor, pages FROM x_query_runs").fetchone()[:] == ("PARTIAL", "c1", 1)
    state["fail"] = False
    r = col.collect(Q, T0, T0 + timedelta(days=1))[0]
    assert r["status"] == "COMPLETE" and r["pages"] == 2 and [p["cursor"] for p in f.requests] == ["", "c1", "c1"]   # page one is not requested again
    assert db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 2


def test_refetch_never_backdates_or_rewrites_first_seen(db, cfg):
    p = provider.normalize_twitterapi_io(tweet(1, T0, text="$QQQ up"), now="2026-11-22T10:00:00Z", batch="b1", mode=provider.RETRO)
    assert p["created_at_utc"] == "2026-01-05T00:00:00Z" and p["first_seen_at_utc"] == p["fetched_at_utc"] == "2026-11-22T10:00:00Z"
    assert p["ingest_mode"] == "RETROSPECTIVE_VISIBLE_NOW" and p["engagement_snapshot"]["snapshot_at_utc"] == "2026-11-22T10:00:00Z"
    assert store.put_post(db, p)
    later = provider.normalize_twitterapi_io(tweet(1, T0, text="$QQQ down (edited)"), now="2026-11-23T10:00:00Z", batch="b2", mode=provider.RETRO)
    assert not store.put_post(db, later)
    row = db.execute("SELECT first_seen_at_utc, text, edit_status FROM x_posts").fetchone()
    assert row[:] == ("2026-11-22T10:00:00Z", "$QQQ up", "CHANGED_AFTER_FIRST_SEEN_VERSION_UNKNOWN")


def test_retweet_quote_and_reply_kinds():
    n = lambda **kw: provider.normalize_twitterapi_io(tweet(1, T0, **kw), now="2026-11-22T10:00:00Z", batch="b", mode=provider.RETRO)
    rt = n(text="RT @a: $QQQ to the moon", isRetweet=True, retweeted_tweet={"id": "9"})
    assert rt["kind"] == "retweet" and rt["author_text"] == ""
    assert n(isQuote=True, quoted_tweet={"id": "9", "text": "bullish"})["kind"] == "quote"
    rp = n(isReply=True, inReplyToId="7")
    assert rp["kind"] == "reply" and rp["parent_available"] is False


# ---------- local import ----------
def test_local_import_jsonl_and_csv_is_retrospective(db, tmp_path):
    j = tmp_path / "a.jsonl"
    j.write_text("\n".join(json.dumps(r) for r in [
        {"post_id": "1", "author_id": "a", "created_at_utc": "2024-03-01T15:00:00Z", "text": "$QQQ looks strong", "lang": "en", "first_seen_at_utc": "2024-03-01T15:00:05Z"},
        {"post_id": "2", "author_id": "b", "created_at_utc": "2024-03-01 15:00:00", "text": "no offset"},
        {"post_id": "", "author_id": "c", "created_at_utc": "2024-03-01T15:00:00Z", "text": "no id"}]), encoding="utf-8")
    out = provider.import_local(db, j, now="2026-11-22T12:00:00Z")
    assert out["inserted"] == 1 and out["rejected"] == {"CREATED_AT_WITHOUT_UTC_OFFSET": 1, "MISSING_ID": 1} and out["first_seen_claims_ignored"] == 1
    row = db.execute("SELECT created_at_utc, first_seen_at_utc, ingest_mode FROM x_posts").fetchone()
    assert row[:] == ("2024-03-01T15:00:00Z", "2026-11-22T12:00:00Z", "RETROSPECTIVE_VISIBLE_NOW")      # the file's own first_seen is not believed
    c = tmp_path / "b.csv"
    c.write_text("post_id,author_id,created_at_utc,text,lang,is_retweet\n1,a,2024-03-01T15:00:00Z,$QQQ looks strong,en,0\n3,d,2024-03-02T15:00:00+00:00,RT @x: hi,en,true\n", encoding="utf-8")
    out = provider.import_local(db, c, now="2026-11-22T12:05:00Z")
    assert out["inserted"] == 1 and out["duplicates"] == 1
    assert db.execute("SELECT kind FROM x_posts WHERE post_id='3'").fetchone()[0] == "retweet"
    with pytest.raises(ValueError):
        provider.declare_import_coverage(db, topic_id="qqq", lang="en", vocab_version="v", start_utc="a", end_utc="b", status="COMPLETE", batch="x")


def test_secrets_are_redacted_from_any_printed_text():
    provider.register_secret("tw-FAKE-not-a-real-key-000")
    s = provider.redact("failed with X-API-Key: tw-FAKE-not-a-real-key-000 and cookie=abc123")
    assert "tw-FAKE" not in s and "abc123" not in s


def test_low_disk_pauses_collection_and_deletes_nothing(db, cfg):
    reg(db)
    p = provider.normalize_twitterapi_io(tweet(1, T0), now="2026-11-22T10:00:00Z", batch="b", mode=provider.RETRO); store.put_post(db, p); db.commit()
    f = Fake(fn=lambda s, e, c: pytest.fail("a request was sent with the disk below the threshold"))
    out = provider.Collector(db, cfg, budget=budget(), transport=f, free_gib=lambda: 19.9).collect(Q, T0, T0 + timedelta(days=2))
    assert out == [{"stopped": "DISK_LOW_COLLECTION_PAUSED"}] and f.requests == []
    assert db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 1
