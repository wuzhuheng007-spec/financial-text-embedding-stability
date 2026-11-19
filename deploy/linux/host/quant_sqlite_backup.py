"""SQLite backup and isolated restore verification (runs inside the app image as uid 10001).

backup SRC_DIR OUT_DIR
    Online backup (sqlite3 backup API) of every *.sqlite3 under SRC_DIR into OUT_DIR, then the
    copy is switched to journal_mode=DELETE (a single self-contained file) and quick_check'ed.
    MANIFEST.json records, per database, row counts and a content digest of the COPY, and the
    row counts of the SOURCE taken right after the backup (the caller holds the data lock, so the
    SEC importer cannot write in between; collectors that write continuously may differ).
verify BACKUP_DIR RESTORE_DIR
    Copies each backed-up database into RESTORE_DIR (an isolated scratch directory), re-runs
    quick_check, recomputes counts and digests and compares them with MANIFEST.json. For
    official_filings.sqlite3 it also reads the restored file through the portal's own reader
    and reports instruments, filings, scope flags, batches and source state.
"""
import hashlib, json, pathlib, shutil, sqlite3, sys

# No row limit: every table is digested in full, streaming. (Until 2026-11-19 a 200,000-row cap
# left larger tables with a NULL digest that could not be checked.)


def summarize(path: pathlib.Path, *, uri_ro: bool) -> dict:
    target = f"file:{path.as_posix()}?mode=ro" if uri_ro else str(path)
    con = sqlite3.connect(target, uri=uri_ro, timeout=60)
    try:
        out = {}
        for (name,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
            h, n = hashlib.sha256(), 0
            cols = [r[1] for r in con.execute(f'pragma table_info("{name}")')]
            q = ",".join(chr(34) + c + chr(34) for c in cols)
            for row in con.execute(f'select {q} from "{name}" order by {q}'):
                h.update(json.dumps(row, default=str).encode()); h.update(b"\n"); n += 1
            out[name] = {"rows": n, "sha256": h.hexdigest()}
        return out
    finally:
        con.close()


def backup(src: pathlib.Path, out: pathlib.Path) -> dict:
    manifest = {}
    for db in sorted(src.rglob("*.sqlite3")):
        rel = db.relative_to(src).as_posix()
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        s, d = sqlite3.connect(db, timeout=60), sqlite3.connect(target)
        try:
            s.backup(d)
        finally:
            s.close(); d.close()
        con = sqlite3.connect(target)
        con.execute("pragma journal_mode=DELETE")
        check = con.execute("pragma quick_check").fetchone()[0]
        con.close()
        for side in ("-wal", "-shm"):
            pathlib.Path(str(target) + side).unlink(missing_ok=True)
        manifest[rel] = {"quick_check": check, "bytes": target.stat().st_size,
                         "copy": summarize(target, uri_ro=True),
                         "source_rows_at_backup": {t: v["rows"] for t, v in summarize(db, uri_ro=False).items()}}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    return {rel: {"quick_check": m["quick_check"], "bytes": m["bytes"]} for rel, m in manifest.items()}


def verify(backup_dir: pathlib.Path, restore_dir: pathlib.Path) -> dict:
    manifest = json.loads((backup_dir / "MANIFEST.json").read_text())
    result, ok = {}, True
    for rel, m in manifest.items():
        restored = restore_dir / rel
        restored.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_dir / rel, restored)
        con = sqlite3.connect(restored)
        check = con.execute("pragma quick_check").fetchone()[0]
        con.close()
        now = summarize(restored, uri_ro=False)
        # A missing (NULL) digest is never a match: it means the table was not checked.
        no_digest = sorted({t for t, v in list(m["copy"].items()) + list(now.items()) if not v.get("sha256")})
        same_copy = now == m["copy"] and not no_digest
        source_diff = {t: [m["source_rows_at_backup"].get(t), now.get(t, {}).get("rows")]
                       for t in set(m["source_rows_at_backup"]) | set(now)
                       if m["source_rows_at_backup"].get(t) != now.get(t, {}).get("rows")}
        entry = {"quick_check": check, "matches_backup_manifest": same_copy, "tables_without_digest": no_digest,
                 "row_count_differences_vs_source_at_backup": source_diff,
                 "rows": {t: v["rows"] for t, v in now.items()}}
        if rel.endswith("official_filings.sqlite3"):
            sys.path.insert(0, "/app")
            from scripts.quant_cloud_official.sec_filings import read_for_portal
            con = sqlite3.connect(restored)
            symbols = {r[0] for r in con.execute("select symbol from instruments")}
            scope = con.execute("select count(*), sum(in_scope), sum(in_scope and in_window), sum(1-in_scope),"
                                " sum(form='NPORT-P' and in_scope=1) from filings").fetchone()
            batches = con.execute("select count(*), sum(status='SUCCESS'), max(batch_id) from batches").fetchone()
            con.close()
            view = read_for_portal(restored, symbols)
            entry["official_filings"] = {
                "instruments": sorted(symbols), "filings_total": scope[0], "in_scope": scope[1],
                "in_scope_in_window": scope[2], "out_of_scope": scope[3], "nport_p_in_scope": scope[4],
                "batches": batches[0], "successful_batches": batches[1], "latest_batch": batches[2],
                "portal_reader_state": view["state"], "portal_reader_filings": len(view["filings"]),
                "portal_reader_instruments": len(view["instruments"]),
                "data_as_of": (view["source_state"] or {}).get("data_as_of"),
                "every_filing_has_sec_links": all(f["index_url"].startswith("https://www.sec.gov/") for f in view["filings"]),
                "every_instrument_has_source": all(i["source_url"] and i["source_sha256"] for i in view["instruments"])}
            entry_ok = (scope[4] == 0 and view["state"] in ("SUCCESS", "PARTIAL")
                        and len(view["filings"]) == scope[1] and len(view["instruments"]) == len(symbols))
        else:
            entry_ok = True
        entry["ok"] = check == "ok" and same_copy and not (rel.endswith("official_filings.sqlite3") and source_diff) and entry_ok
        ok = ok and entry["ok"]
        result[rel] = entry
    return {"ok": ok, "databases": result}


if __name__ == "__main__":
    mode, a, b = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
    res = backup(a, b) if mode == "backup" else verify(a, b)
    print(json.dumps(res))
    sys.exit(0 if mode == "backup" or res["ok"] else 1)
