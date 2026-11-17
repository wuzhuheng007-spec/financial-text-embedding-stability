"""Add read-completion evidence without changing legacy ingestion timestamps."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .preflight import PRIVATE, PUBLIC, save, sha
from .store import DB, init, provider_response_time, utc_now


def main():
    private_path = PRIVATE / "response_time_migration_private.json"
    if private_path.exists():
        raise FileExistsError("RESPONSE_TIME_MIGRATION_ALREADY_RECORDED")
    init(DB)
    audit = []
    with closing(sqlite3.connect(DB)) as con:
        rows = con.execute("""SELECT provider,article_id,version,raw_response_path,
                    first_received_at_utc,response_received_at_utc FROM articles
                    ORDER BY provider,article_id,version""").fetchall()
        for provider, aid, version, raw_path, ingest_time, old_response_time in rows:
            evidence = provider_response_time(Path(raw_path)) if Path(raw_path).is_file() else None
            if evidence and old_response_time is None:
                con.execute("""UPDATE articles SET response_received_at_utc=?
                    WHERE provider=? AND article_id=? AND version=? AND response_received_at_utc IS NULL""",
                    (evidence, provider, aid, version))
            audit.append({"provider": provider, "article_id": aid, "version": version,
                          "legacy_ingest_at_utc": ingest_time,
                          "provider_response_read_completed_at_utc": evidence,
                          "original_response_time_value": old_response_time})
        con.commit()
    save(private_path, {"schema": "news_v4_response_time_migration_private_1",
                        "migrated_at_utc": utc_now(), "rows": audit})
    receipt = {"schema": "news_v4_response_time_migration_redacted_1",
               "migrated_at_utc": utc_now(), "article_versions_checked": len(audit),
               "response_completion_found": sum(bool(x["provider_response_read_completed_at_utc"]) for x in audit),
               "previously_present": sum(bool(x["original_response_time_value"]) for x in audit),
               "missing_response_evidence": sum(not x["provider_response_read_completed_at_utc"] for x in audit),
               "legacy_ingestion_time_preserved": True,
               "private_audit_sha256": sha(private_path)}
    save(PUBLIC / "response_time_migration_redacted.json", receipt)
    return receipt


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
