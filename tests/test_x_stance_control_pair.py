"""The isolated, paired like-floor control. Fake transports only; no request leaves the machine.

Reproduces the two defects found in the first version (2026-11-26) and checks they are gone:
  1. a side that failed on its first window was not asked on that window again (its own cursor moved on later), and
     with an older cursor the two sides could ask windows of different widths;
  2. control posts entered the production store, where the first like snapshot decides the sample."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.x_stance_v1 import config as C, control_pair, incremental, provider, stance, store
from scripts.x_stance_v1.ledger import Ledger

UTC = timezone.utc
T0 = datetime(2026, 11, 26, 2, 20, 30, tzinfo=UTC)


def tweet(i, ts, likes):
    return {"id": str(i), "url": f"https://x.com/u/status/{i}", "text": f"$QQQ looks fine {i}", "lang": "en", "likeCount": likes,
            "createdAt": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"), "author": {"id": str(9000 + i), "userName": f"user{i}"}}


class Net:
    """Answers each query with posts inside its window: the floor query gets 25 likes, the plain one 5."""

    def __init__(self, fail=None, base=0, clock_box=None):
        self.requests, self.fail, self.n, self.clock_box = [], dict(fail or {}), base, clock_box

    def get(self, params):
        self.requests.append(dict(params))
        if self.clock_box is not None:
            self.clock_box[0] += timedelta(minutes=30)            # every call takes half an hour of the locked clock
        ex = self.fail.get(len(self.requests))
        if ex:
            raise ex
        q = params["query"]
        s, e = (int(q.split(k)[1].split()[0]) for k in ("since_time:", "until_time:"))
        likes = 25 if "min_faves:20" in q else 5
        out = []
        for j in range(3):
            self.n += 1
            out.append(tweet(self.n, datetime.fromtimestamp(e - 1 - j, UTC), likes))
        return {"tweets": out, "has_next_page": False, "next_cursor": ""}


def windows(net):
    return [tuple(int(r["query"].split(k)[1].split()[0]) for k in ("since_time:", "until_time:")) for r in net.requests]


@pytest.fixture()
def cfg():
    c = C.load()
    c["topics"] = [t for t in c["topics"] if t["id"] == "qqq"]; c["languages"] = ["en"]
    c["incremental"] = {**c["incremental"], "include_watchlist": False}
    c["incremental"]["control_pairs"] = [{**c["incremental"]["control_pairs"][0], "active_until_utc": "2026-11-27T00:00:00Z"}]
    c["acquisition"]["retry"] = {**(c["acquisition"].get("retry") or {}), "max_attempts_per_unit_per_round": 1}
    return c


@pytest.fixture()
def dbs(tmp_path):
    db = store.connect(tmp_path / "x.sqlite3"); adb = control_pair.connect_audit(control_pair.audit_path(tmp_path / "x.sqlite3"))
    yield db, adb
    db.close(); adb.close()


def ctl_run(db, adb, cfg, net, t, clock=None):
    return control_pair.run(db, adb, cfg, net, t, ledger=Ledger(db, cfg, clock=clock or (lambda: t)), clock=clock or (lambda: t))


def test_the_increment_no_longer_asks_the_control_itself(dbs, cfg):
    db, _ = dbs
    assert not [u for u in incremental.units(db, cfg, T0) if u.get("control")]


def test_control_pages_and_posts_go_to_the_audit_store_only(dbs, cfg):
    db, adb = dbs
    out = ctl_run(db, adb, cfg, Net(), T0)
    assert out["pairs"][0]["state"] == "COMPLETE" and out["requests"] == 2
    assert db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 0                       # production store: no control post
    assert db.execute("SELECT count(*) FROM x_page_cache").fetchone()[0] == 0
    assert adb.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 6
    assert db.execute("SELECT count(*) FROM x_cost_ledger WHERE status='SETTLED'").fetchone()[0] == 2   # but production pays and sees the bill


def test_both_sides_ask_exactly_the_same_window(dbs, cfg):
    db, adb = dbs
    net = Net(); ctl_run(db, adb, cfg, net, T0)
    w = windows(net); assert len(w) == 2 and w[0] == w[1]
    end = int(T0.replace(second=0).timestamp()) - 6 * 3600
    assert w[0] == (end - 7200, end)
    p = adb.execute("SELECT * FROM x_control_pairs").fetchone()
    assert p["pair_id"] == control_pair.pair_id(cfg["incremental"]["control_pairs"][0], end - 7200, end)


def test_defect_1_a_first_failure_is_retried_on_its_own_window_and_only_that_side(dbs, cfg):
    db, adb = dbs
    e503 = provider.ProviderHTTPError(503, "HTTP_503_NOT_CHARGED")
    net = Net(fail={2: e503}); a = ctl_run(db, adb, cfg, net, T0)                      # 'floor' sorts first, 'no_threshold' fails
    assert a["pairs"][0]["state"] == "PARTIAL"
    t1 = T0 + timedelta(hours=2); net2 = Net(base=100); b = ctl_run(db, adb, cfg, net2, t1)
    assert len(net2.requests) == 1 and "min_faves" not in net2.requests[0]["query"]         # only the failed side
    assert windows(net2)[0] == windows(net)[0]                                               # on the window it failed on
    assert b["pairs"][0]["state"] == "COMPLETE" and b["pairs"][0]["pair_id"] == a["pairs"][0]["pair_id"]
    t2 = t1 + timedelta(hours=2); net3 = Net(base=200); ctl_run(db, adb, cfg, net3, t2)
    w = windows(net3); assert len(w) == 2 and w[0] == w[1] and w[0][0] == windows(net)[0][1]   # next pair: contiguous, same width on both sides
    assert w[0][1] - w[0][0] == 7200


def test_a_long_outage_keeps_the_pair_width_and_records_the_gap(dbs, cfg):
    db, adb = dbs
    ctl_run(db, adb, cfg, Net(), T0)
    late = T0 + timedelta(hours=11); net = Net(base=50); ctl_run(db, adb, cfg, net, late)
    w = windows(net); assert w[0] == w[1] and w[0][1] - w[0][0] == 7200
    rule = json.loads(adb.execute("SELECT rule_json FROM x_control_pairs ORDER BY window_start_utc DESC LIMIT 1").fetchone()[0])
    assert rule["gap_before_seconds"] == 9 * 3600


def test_restart_after_one_side_succeeded_asks_only_the_other(dbs, cfg):
    db, adb = dbs

    class Dies(Net):
        def get(self, params):
            if len(self.requests) == 1:
                raise KeyboardInterrupt                                                      # the process is killed after the first side
            return super().get(params)
    with pytest.raises(KeyboardInterrupt):
        ctl_run(db, adb, cfg, Dies(), T0)
    net = Net(base=10); out = ctl_run(db, adb, cfg, net, T0 + timedelta(minutes=5))
    assert len(net.requests) == 1 and out["pairs"][0]["state"] == "COMPLETE"


def test_a_402_on_one_side_leaves_that_side_open_on_its_window(dbs, cfg):
    db, adb = dbs
    net = Net(fail={2: provider.ProviderHTTPError(402, "HTTP_402")})
    out = ctl_run(db, adb, cfg, net, T0)
    sides = {r["variant"]: r["status"] for r in adb.execute("SELECT variant, status FROM x_control_sides")}
    assert out["pairs"][0]["state"] == "PARTIAL" and sides["floor"] in control_pair.DONE and sides["no_threshold"] not in control_pair.DONE


def test_the_cutoff_is_enforced_in_front_of_every_network_call(dbs, cfg):
    db, adb = dbs
    cfg["incremental"]["control_pairs"][0]["active_until_utc"] = store.iso(T0 + timedelta(minutes=20))
    box = [T0]; net = Net(clock_box=box)
    out = ctl_run(db, adb, cfg, net, T0, clock=lambda: box[0])
    assert len(net.requests) == 1 and out["refused_after_cutoff"] == 1                       # the second call crossed the cut-off and was refused
    later = T0 + timedelta(hours=2); net2 = Net()
    out2 = ctl_run(db, adb, cfg, net2, later, clock=lambda: later)
    assert net2.requests == [] and out2["pairs"][0]["state"] == "CUTOFF"
    assert adb.execute("SELECT count(*) FROM x_control_pairs WHERE state='CUTOFF'").fetchone()[0] == 1


def test_defect_2_control_only_posts_already_in_production_never_reach_a_sample(dbs, cfg):
    db, adb = dbs
    p = cfg["incremental"]["control_pairs"][0]
    qh = control_pair.query_hash(cfg, p, "floor")
    db.execute("INSERT INTO x_queries VALUES(?,?,?,?,?,?)", (qh, cfg["provider_candidate"], "qqq", "en", f"control:{p['name']}:floor", "x"))
    col = provider.Collector(db, cfg, budget=__import__("scripts.x_stance_v1.ledger", fromlist=["x"]).JobBudget(Ledger(db, cfg, clock=lambda: T0), pool="recent", job_type="recent"),
                             transport=Net(), mode=provider.LIVE, clock=lambda: T0)
    end = int(T0.timestamp()) - 6 * 3600
    col.collect_pages({"query_hash": qh, "topic_id": "qqq", "lang": "en", "query": "$QQQ -filter:replies lang:en min_faves:20"}, end - 7200, end, 1)   # as the first version did
    assert db.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 3
    assert len(control_pair.control_only_posts(db)) == 3
    n = stance.map_sample(db, cfg)
    assert n["control_only"] == 3 and db.execute("SELECT count(*) FROM x_post_topics").fetchone()[0] == 0


def test_migration_rebuilds_the_first_versions_pairs_in_the_audit_store(dbs, cfg):
    db, adb = dbs
    p = cfg["incremental"]["control_pairs"][0]
    from scripts.x_stance_v1.ledger import JobBudget
    end = int(T0.timestamp()) - 6 * 3600
    for v in control_pair.VARIANTS:
        qh = control_pair.query_hash(cfg, p, v)
        db.execute("INSERT INTO x_queries VALUES(?,?,?,?,?,?)", (qh, cfg["provider_candidate"], "qqq", "en", f"control:{p['name']}:{v}", control_pair.texts(p)[v]))
        col = provider.Collector(db, cfg, budget=JobBudget(Ledger(db, cfg, clock=lambda: T0), pool="recent", job_type="recent"), transport=Net(base=10 if v == "floor" else 0), mode=provider.LIVE, clock=lambda: T0)
        col.collect_pages({"query_hash": qh, "topic_id": "qqq", "lang": "en", "query": control_pair.texts(p)[v]}, end - 7200, end, 1)
    m = control_pair.migrate_from_production(db, adb, cfg)
    assert m["pairs"] == 1 and m["pages"] == 2 and adb.execute("SELECT state FROM x_control_pairs").fetchone()[0] == "COMPLETE"
    assert adb.execute("SELECT count(*) FROM x_posts").fetchone()[0] == 6
