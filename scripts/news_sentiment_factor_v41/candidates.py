"""Observed-cache candidate series. No prices, return labels, or fitting."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .freeze import PRIVATE, PUBLIC, now, sha, write
from .score import DB


def day_shanghai(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("NAIVE_RECEIPT_TIME")
    return parsed.astimezone(timezone(timedelta(hours=8))).date().isoformat()


def main() -> dict:
    target = PUBLIC / "candidate_coverage_redacted.json"
    if target.exists():
        raise FileExistsError(target)
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""SELECT a.provider,a.article_id,a.version,a.received_at_utc,a.cohort,
          a.event_group_id,s.segment_id,s.kind,s.company_association,s.association_status,
          s.semantic_kind_candidate,x.net_score,x.status,x.scored_at_utc
          FROM articles a JOIN segments s USING(provider,article_id,version)
          JOIN scores x USING(provider,article_id,version,segment_id,segment_rule_version)
          ORDER BY a.received_at_utc,a.provider,a.article_id,a.version,s.segment_id""").fetchall()
    by_article_company: dict[tuple, list] = defaultdict(list)
    associations = Counter()
    kinds = Counter()
    for r in rows:
        associations[r["association_status"]] += 1
        kinds[r["kind"]] += 1
        if r["status"] != "DONE" or r["association_status"] != "ONE_EXPLICIT_COMPANY":
            continue
        key = (r["provider"], r["article_id"], r["version"], r["company_association"])
        by_article_company[key].append(r)
    # One article contributes at most one body-based tone per explicitly linked company.
    # Its title is a fallback only when there is no company-linked body segment.
    event_inputs = []
    for (provider, article_id, version, company), article_rows in by_article_company.items():
        body = [x for x in article_rows if x["kind"] == "BODY_SEGMENT"]
        chosen = body if body else [x for x in article_rows if x["kind"] == "TITLE"]
        representative = chosen[0]
        event_inputs.append({"provider": provider, "article_id": article_id, "version": version,
            "entity_id": company, "event_group_id": representative["event_group_id"],
            "received_at_utc": representative["received_at_utc"], "cohort": representative["cohort"],
            "source_kind": "BODY_SEGMENT" if body else "TITLE_ONLY",
            "scored_at_latest_utc": max(x["scored_at_utc"] for x in chosen),
            "tone": sum(x["net_score"] for x in chosen) / len(chosen),
            "segment_count": len(chosen)})
    by_event: dict[tuple, list] = defaultdict(list)
    for x in event_inputs:
        # A day is a local receipt day, never an inferred market trading day.
        key = (x["cohort"], day_shanghai(x["received_at_utc"]), x["entity_id"],
               x["provider"], x["event_group_id"])
        by_event[key].append(x)
    event_rows = []
    for key, group in by_event.items():
        # Same algorithmic event and same article revisions contribute once.
        by_article = {}
        for x in group:
            old = by_article.get(x["article_id"])
            if old is None or (x["version"], x["received_at_utc"]) > (old["version"], old["received_at_utc"]):
                by_article[x["article_id"]] = x
        kept = list(by_article.values())
        event_rows.append({"cohort": key[0], "received_day_shanghai": key[1], "entity_id": key[2],
                           "provider": key[3], "event_group_id": key[4],
                           "article_versions_used": len(kept),
                           "tone": sum(x["tone"] for x in kept) / len(kept),
                           "latest_scored_at_utc": max(x["scored_at_latest_utc"] for x in kept),
                           "grouping_status": "ALGORITHMIC_PROXY_NOT_VERIFIED_EVENT"})
    by_company_day: dict[tuple, list] = defaultdict(list)
    for x in event_rows:
        by_company_day[(x["cohort"], x["received_day_shanghai"], x["entity_id"])].append(x)
    panel = []
    for (cohort, day, company), events in sorted(by_company_day.items()):
        panel.append({"cohort": cohort, "received_day_shanghai": day, "entity_id": company,
                      "N1_observed_event_tone_mean": sum(e["tone"] for e in events) / len(events),
                      "N2_observed_event_group_count": len(events),
                      "latest_scored_at_utc": max(e["latest_scored_at_utc"] for e in events),
                      "incomplete_source_coverage": True,
                      "provisional_current_day": day == day_shanghai(now()),
                      "not_a_trading_day_panel": True})
    write(PRIVATE / "candidate_event_inputs_private.json", event_inputs)
    write(PRIVATE / "candidate_event_groups_private.json", event_rows)
    write(PRIVATE / "candidate_company_day_private.json", panel)
    public = {"schema": "news_sentiment_factor_v41_candidate_coverage_redacted_1",
              "created_at_utc": now(), "as_of_utc": now(),
              "scope": "OBSERVED_PRIVATE_CACHE_ONLY_NOT_COMPLETE_NEWS_ARCHIVE",
              "total_scored_segments": len(rows), "segment_association_status": dict(associations),
              "segment_kinds": dict(kinds), "article_company_inputs": len(event_inputs),
              "algorithmic_company_event_groups": len(event_rows), "observed_company_day_rows": len(panel),
              "companies_in_panel": len({x["entity_id"] for x in panel}),
              "receipt_days_in_panel": len({x["received_day_shanghai"] for x in panel}),
              "cohorts": dict(Counter(x["cohort"] for x in panel)),
              "event_grouping_is_not_independent_confirmation": True,
              "N1": "mean of company-explicit body segments per article, or title fallback; equal mean by algorithmic event group",
              "N2": "number of distinct observed algorithmic event groups for the company and China receipt date",
              "no_news_versus_missing_coverage": "cannot distinguish from current incomplete source cache",
              "trading_day_mapping": "NOT_PERFORMED_RECEIPT_CIVIL_DAY_ONLY",
              "model_training_cutoff": "NOT_DISCLOSED",
              "private_event_inputs_sha256": sha(PRIVATE / "candidate_event_inputs_private.json"),
              "private_event_groups_sha256": sha(PRIVATE / "candidate_event_groups_private.json"),
              "private_company_day_sha256": sha(PRIVATE / "candidate_company_day_private.json"),
              "no_returns_or_models_fitted": True}
    write(target, public)
    return public


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
