import gzip
import json
from datetime import datetime, timezone

from scripts.qqq_x_10y import probe_summary as S

S0 = int(datetime(2020, 3, 2, 16, 0, tzinfo=timezone.utc).timestamp())
E0 = S0 + 3600


def tw(i, minute=10, text="$QQQ looks weak", lang="en", author="a1", reply=False):
    t = datetime.fromtimestamp(S0 + minute * 60, timezone.utc).strftime("%a %b %d %H:%M:%S +0000 %Y")
    d = {"id": i, "createdAt": t, "text": text, "lang": lang, "isReply": reply}
    if author is not None:
        d["author"] = {"id": author}
    return d


def test_three_id_levels_differ():
    s = S.unit_sets([tw("1"), tw("2", minute=70), tw("3", text="$TQQQ only"), tw("4", reply=True), {"text": "no id"}], S0, E0)
    assert set(s["ALL"]) == {"1", "2", "3", "4"} and set(s["HOUR"]) == {"1", "3", "4"} and set(s["MAIN"]) == {"1"}
    assert s["_no_id"] == 1


def test_intersection_union_and_exclusive_counts_per_level():
    c = S.unit_sets([tw("1"), tw("2"), tw("9", minute=70)], S0, E0)
    b = S.unit_sets([tw("2"), tw("3", text="QQQ calls"), tw("9", minute=70)], S0, E0)
    r_all = S.compare(c["ALL"], b["ALL"], "COMPLETE", "COMPLETE")
    r_main = S.compare(c["MAIN"], b["MAIN"], "COMPLETE", "COMPLETE")
    assert (r_all["cash_only"], r_all["bare_only"], r_all["both"], r_all["union"]) == (1, 1, 2, 4)
    assert (r_main["cash_only"], r_main["bare_only"], r_main["both"], r_main["union"]) == (1, 1, 1, 3)
    assert abs(r_main["bare_new_share"] - 1 / 3) < 1e-12 and r_main["pairing"] == "PAIRED_COMPLETE"


def test_pairing_rules_truncated_is_lower_bound_half_missing_is_na_and_zero_union_is_na():
    c = S.unit_sets([tw("1")], S0, E0)["MAIN"]
    assert S.compare(c, c, "COMPLETE", "TRUNCATED")["pairing"] == "PAIRED_LOWER_BOUND"
    r = S.compare(c, None, "COMPLETE", "FAILED")
    assert r["pairing"] == "HALF_MISSING" and r["union"] is None and r["bare_new_share"] is None
    assert S.compare({}, {}, "COMPLETE", "COMPLETE")["bare_new_share"] is None
    assert S.na(None) == "NA"


def test_authors_count_only_non_empty_ids():
    s = S.unit_sets([tw("1", author="x"), tw("2", author="x"), tw("3", author=None), tw("4", author="")], S0, E0)
    r = S.compare(s["MAIN"], {}, "COMPLETE", "COMPLETE")
    assert (r["union_authors"], r["union_missing_author"]) == (1, 2)


def test_summary_reads_only_existing_gzip_pages(tmp_path):
    (tmp_path / "pages").mkdir()
    units = [{"unit_id": "2020-03:Q_CASH", "month": "2020-03", "day": "2020-03-02", "query_id": "Q_CASH", "since_time": S0, "until_time": E0},
             {"unit_id": "2020-03:Q_BARE", "month": "2020-03", "day": "2020-03-02", "query_id": "Q_BARE", "since_time": S0, "until_time": E0},
             {"unit_id": "2020-04:Q_CASH", "month": "2020-04", "day": "2020-04-01", "query_id": "Q_CASH", "since_time": S0, "until_time": E0},
             {"unit_id": "2020-04:Q_BARE", "month": "2020-04", "day": "2020-04-01", "query_id": "Q_BARE", "since_time": S0, "until_time": E0}]
    for name, ts in (("2020-03_Q_CASH_01", [tw("1"), tw("2")]), ("2020-03_Q_BARE_01", [tw("2"), tw("3", text="QQQ puts")]),
                     ("2020-04_Q_CASH_01", [tw("5")])):
        with gzip.open(tmp_path / "pages" / f"{name}.json.gz", "wt", encoding="utf-8") as f:
            json.dump({"tweets": ts, "has_next_page": False, "next_cursor": None}, f)
    ust = {"2020-03:Q_CASH": ("COMPLETE", 1, None), "2020-03:Q_BARE": ("COMPLETE", 1, None),
           "2020-04:Q_CASH": ("COMPLETE", 1, None), "2020-04:Q_BARE": ("FAILED", 1, "HTTP_503_NOT_CHARGED")}
    rows, by_year, _ = S.summarise(units, tmp_path, ust)
    assert rows[0]["pairing"] == "PAIRED_COMPLETE" and rows[0]["main_union"] == 3 and rows[0]["main_bare_only"] == 1
    assert rows[0]["main_bare_only_bare_word_only"] == 1 and rows[0]["main_bare_only_text_has_cashtag"] == 0   # "QQQ puts"
    assert rows[1]["main_bare_word_gain_share"] is None
    assert rows[1]["pairing"] == "HALF_MISSING" and rows[1]["main_union"] is None
    y = by_year["2020"]
    assert (y["planned_months"], y["paired_complete"], y["half_missing"]) == (2, 1, 1) and y["main_union_mean_per_hour"] == 3.0
