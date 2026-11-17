"""Read-only owner research summary. No model or provider calls occur here."""
from __future__ import annotations

import json
import os
import sqlite3
import ctypes
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit


def safe_link(value: str | None) -> str | None:
    if not value or any(ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
        return value
    return None


def _pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def chinese_research_summary(root: Path, *, debug: bool = False) -> dict:
    path = root / "artifacts/news_sentiment_factor_v41_20261117/chinese_tone.sqlite3"
    worker = root / "results/news_sentiment_factor_v41_20261117/worker_status_redacted.json"
    worker_private = root / "artifacts/news_sentiment_factor_v41_20261117/worker_state_private.json"
    result = {"state": "NO_CHINESE_RESEARCH_CACHE", "research_status": "CANDIDATE_ONLY",
              "article_versions": 0, "scored_segments": 0, "scored_article_versions": 0,
              "prospective_article_versions": 0, "prospective_scored_segments": 0,
              "providers": {}, "association_status": {}, "latest_scored_at_utc": None,
              "rows": [], "worker": {"state": "NOT_STARTED"},
              "scope": "observed private cache; incomplete news archive",
              "model_training_cutoff": "NOT_DISCLOSED",
              "no_return_prediction_or_verified_news_claim": True}
    if worker.is_file():
        try:
            data = json.loads(worker.read_text(encoding="utf-8"))
            result["worker"] = {k: data.get(k) for k in ("state", "last_checked_at_utc", "new_versions",
                              "scored_units", "failures", "daily_limit", "daily_new_versions", "backlog_count")}
            if result["worker"]["state"] == "RUNNING":
                private_state = json.loads(worker_private.read_text(encoding="utf-8")) if worker_private.is_file() else {}
                if private_state.get("run_id") != data.get("run_id") or not _pid_alive(private_state.get("pid")):
                    result["worker"]["state"] = "STALE_EXIT"
        except (json.JSONDecodeError, OSError):
            result["worker"] = {"state": "STATUS_UNAVAILABLE"}
    if not path.is_file():
        return result
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)) as con:
            con.row_factory = sqlite3.Row
            result["state"] = "READY"
            result["article_versions"] = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            result["scored_segments"] = con.execute("SELECT COUNT(*) FROM scores WHERE status='DONE'").fetchone()[0]
            result["scored_article_versions"] = con.execute("""SELECT COUNT(*) FROM articles a WHERE EXISTS
                (SELECT 1 FROM scores s WHERE s.provider=a.provider AND s.article_id=a.article_id
                 AND s.version=a.version AND s.status='DONE')""").fetchone()[0]
            result["prospective_article_versions"] = con.execute("SELECT COUNT(*) FROM articles WHERE cohort='PROSPECTIVE_RECEIPT'").fetchone()[0]
            result["prospective_scored_segments"] = con.execute("SELECT COUNT(*) FROM scores WHERE status='DONE' AND score_origin='PROSPECTIVE_RECEIPT'").fetchone()[0]
            result["providers"] = {r[0]: r[1] for r in con.execute("SELECT provider,COUNT(*) FROM articles GROUP BY provider")}
            result["association_status"] = {r[0]: r[1] for r in con.execute(
                "SELECT association_status,COUNT(*) FROM segments GROUP BY association_status")}
            result["latest_scored_at_utc"] = con.execute("SELECT MAX(scored_at_utc) FROM scores WHERE status='DONE'").fetchone()[0]
            latest = con.execute("""SELECT a.provider,a.article_id,a.version,a.publisher,a.source_url,a.published_at_utc,
                a.received_at_utc,a.cohort,MAX(s.scored_at_utc) AS latest_score
                FROM articles a JOIN scores s USING(provider,article_id,version)
                WHERE s.status='DONE' GROUP BY a.provider,a.article_id,a.version
                ORDER BY (a.cohort='PROSPECTIVE_RECEIPT') DESC,latest_score DESC LIMIT 10""").fetchall()
            for article in latest:
                rows = con.execute("""SELECT x.kind,x.company_association,x.association_status,
                    x.entity_candidates_json,s.net_score,s.scored_at_utc
                    FROM segments x JOIN scores s USING(provider,article_id,version,segment_id,segment_rule_version)
                    WHERE x.provider=? AND x.article_id=? AND x.version=? AND s.status='DONE'""",
                    (article["provider"], article["article_id"], article["version"])).fetchall()
                body = [r for r in rows if r["kind"] == "BODY_SEGMENT"]
                used = body if body else [r for r in rows if r["kind"] == "TITLE"]
                company_refs = []
                for r in used:
                    if r["association_status"] == "ONE_EXPLICIT_COMPANY":
                        for candidate in json.loads(r["entity_candidates_json"]):
                            if candidate["id"] == r["company_association"]:
                                company_refs.append({"entity_id": candidate["id"], "matched_phrase": candidate["phrase"]})
                result["rows"].append({"provider": article["provider"], "publisher": article["publisher"],
                    "source_url": safe_link(article["source_url"]), "published_at_utc": article["published_at_utc"],
                    "received_at_utc": article["received_at_utc"], "scored_at_utc": article["latest_score"],
                    "origin": article["cohort"], "input_kind": "BODY_SEGMENTS" if body else "TITLE_ONLY",
                    "text_tone_score": sum(r["net_score"] for r in used) / len(used) if used else None,
                    "company_refs": list({x["entity_id"]: x for x in company_refs}.values())[:6],
                    "company_association_status": "EXPLICIT_ALIAS_CANDIDATE" if company_refs else "UNASSIGNED",
                    "candidate_only": True})
    except (sqlite3.Error, OSError, ValueError, KeyError, json.JSONDecodeError):
        if debug:
            raise
        result["state"] = "CACHE_UNAVAILABLE"
        result["rows"] = []
    return result
