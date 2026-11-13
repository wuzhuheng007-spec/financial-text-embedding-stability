"""Private, representation-preserving CSV storage, with no identity adjudication.

This module never exports licensed rows to the public metadata catalog. It does
not interpret dates, choose securities, join datasets, adjust prices or compute
labels. Rows contain positional JSON arrays of strings, including empty cells.
"""

from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import zipfile


SCHEMA_VERSION = "1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LocalDatabaseError(ValueError):
    """Fixed error code; messages never contain source values or private paths."""


def _require(condition, code):
    if not condition:
        raise LocalDatabaseError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _frame(value):
    return (_json(value) + "\n").encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_hash(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _public_path(path):
    parts = [part.casefold() for part in path.parts]
    return any(parts[i:i + 2] == ["database", "wrds"] for i in range(len(parts) - 1))


def _private_destination(db_path, output_root):
    path = Path(db_path).expanduser().resolve()
    _require(not _public_path(path), "PUBLIC_DATABASE_DIRECTORY_FORBIDDEN")
    private = ".wrds_private" in path.parent.parts
    if output_root is not None:
        root = Path(output_root).expanduser().resolve()
        _require(path.is_relative_to(root) and path != root,
                 "DATABASE_OUTSIDE_EXPLICIT_OUTPUT_ROOT")
        _require(not _public_path(root), "PUBLIC_DATABASE_DIRECTORY_FORBIDDEN")
        _require(private or not root.is_relative_to(PROJECT_ROOT),
                 "EXPLICIT_OUTPUT_ROOT_MUST_BE_EXTERNAL")
    else:
        _require(private, "PRIVATE_DATABASE_DIRECTORY_REQUIRED")
    return path


def _safe_archive_name(name):
    # No extraction occurs, but reject unsafe/ambiguous archives at the boundary.
    text = name[:-1] if name.endswith("/") else name
    path = PurePosixPath(text)
    return (bool(text) and not path.is_absolute() and "\\" not in text
            and ":" not in text and all(p not in ("", ".", "..") for p in text.split("/")))


def _select_member(archive, member):
    infos = archive.infolist()
    names = [info.filename for info in infos]
    _require(all(_safe_archive_name(name) for name in names), "UNSAFE_ARCHIVE_MEMBER")
    _require(len(names) == len(set(name.casefold() for name in names)),
             "DUPLICATE_ARCHIVE_MEMBER")
    files = [info for info in infos if not info.is_dir()]
    if member is None:
        _require(len(files) == 1, "ARCHIVE_MEMBER_REQUIRED")
        info = files[0]
    else:
        _require(isinstance(member, str) and _safe_archive_name(member), "UNSAFE_ARCHIVE_MEMBER")
        matches = [info for info in files if info.filename == member]
        _require(len(matches) == 1, "ARCHIVE_MEMBER_NOT_FOUND")
        info = matches[0]
    _require(not info.flag_bits & 1, "ENCRYPTED_ARCHIVE_UNSUPPORTED")
    return info


@contextmanager
def _binary_source(path, member, transport):
    if transport == "zip":
        with zipfile.ZipFile(path) as archive:
            info = _select_member(archive, member)
            with archive.open(info) as stream:
                yield stream
    else:
        with path.open("rb") as stream:
            yield stream


def _snapshot_source(path, member=None):
    stat_before = path.stat()
    source_sha, source_bytes = _file_hash(path)
    is_zip = zipfile.is_zipfile(path)
    _require(is_zip or path.suffix.casefold() != ".zip", "INVALID_SOURCE_ARCHIVE")
    transport = "zip" if is_zip else "csv"
    selected = None
    crc = None
    if is_zip:
        with zipfile.ZipFile(path) as archive:
            info = _select_member(archive, member)
            selected, crc = info.filename, f"{info.CRC:08x}"
    else:
        _require(member is None, "MEMBER_ONLY_VALID_FOR_ARCHIVE")
    digest = hashlib.sha256()
    csv_bytes = 0
    with _binary_source(path, selected, transport) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            csv_bytes += len(block)
    stat_after = path.stat()
    signature = lambda s: (s.st_size, s.st_mtime_ns, s.st_ino)
    _require(signature(stat_before) == signature(stat_after), "SOURCE_CHANGED_DURING_IMPORT")
    return {"transport_kind": transport, "source_sha256": source_sha,
            "source_bytes": source_bytes, "member_name": selected,
            "member_crc32": crc, "csv_sha256": digest.hexdigest(),
            "csv_bytes": csv_bytes, "stat_signature": signature(stat_after)}


class _HashingRaw(io.RawIOBase):
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def readable(self):
        return True

    def readinto(self, target):
        block = self.stream.read(len(target))
        self.digest.update(block)
        target[:len(block)] = block
        return len(block)


DDL = (
    "CREATE TABLE local_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE contents (
        content_id INTEGER PRIMARY KEY, csv_sha256 TEXT NOT NULL UNIQUE,
        csv_bytes INTEGER NOT NULL, encoding TEXT NOT NULL,
        header_json TEXT NOT NULL, header_sha256 TEXT NOT NULL,
        column_count INTEGER NOT NULL, exact_duplicate_headers INTEGER NOT NULL,
        normalized_header_collisions INTEGER NOT NULL, blank_headers INTEGER NOT NULL,
        row_count INTEGER NOT NULL, width_mismatch_rows INTEGER NOT NULL,
        records_sha256 TEXT NOT NULL, cells_sha256 TEXT NOT NULL)""",
    """CREATE TABLE headers (
        content_id INTEGER NOT NULL REFERENCES contents(content_id),
        column_ordinal INTEGER NOT NULL, name TEXT NOT NULL,
        PRIMARY KEY(content_id, column_ordinal))""",
    """CREATE TABLE records (
        content_id INTEGER NOT NULL REFERENCES contents(content_id),
        row_ordinal INTEGER NOT NULL, cells_json TEXT NOT NULL,
        cell_count INTEGER NOT NULL, width_matches_header INTEGER NOT NULL,
        record_sha256 TEXT NOT NULL, cells_sha256 TEXT NOT NULL,
        PRIMARY KEY(content_id, row_ordinal))""",
    """CREATE TABLE source_occurrences (
        occurrence_id INTEGER PRIMARY KEY,
        content_id INTEGER NOT NULL REFERENCES contents(content_id),
        dataset_kind TEXT NOT NULL, source_path TEXT NOT NULL,
        transport_kind TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        source_bytes INTEGER NOT NULL, member_name TEXT, member_crc32 TEXT,
        imported_at_utc TEXT NOT NULL)""",
)


def _initialize(connection):
    for statement in DDL:
        connection.execute(statement)
    connection.executemany("INSERT INTO local_metadata VALUES (?,?)", (
        ("schema_version", SCHEMA_VERSION),
        ("privacy", "LOCAL_ROW_DATA_NEVER_PUBLIC_EXPORT"),
        ("interpretation", "RAW_REPRESENTATION_ONLY_NO_ADMISSION"),
    ))


def _check_schema(connection):
    try:
        rows = dict(connection.execute("SELECT key,value FROM local_metadata"))
    except sqlite3.DatabaseError:
        raise LocalDatabaseError("NOT_A_PRIVATE_WRDS_DATABASE") from None
    _require(rows.get("schema_version") == SCHEMA_VERSION
             and rows.get("privacy") == "LOCAL_ROW_DATA_NEVER_PUBLIC_EXPORT",
             "UNSUPPORTED_PRIVATE_DATABASE_SCHEMA")


def _readonly_connection(path, *, require_private_schema=True):
    _require(path.is_file(), "DATABASE_NOT_FOUND")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if require_private_schema:
            _check_schema(connection)
    except Exception:
        connection.close()
        raise
    return connection


def _scan_source(connection, path, snapshot, *, content_id=None):
    """Parse independently, inserting new content or checking every saved record."""
    writing = content_id is None
    saved = None if writing else dict(connection.execute(
        "SELECT * FROM contents WHERE content_id=?", (content_id,)).fetchone())
    record_digest, cell_digest = hashlib.sha256(), hashlib.sha256()
    row_count = mismatch = 0
    with _binary_source(path, snapshot["member_name"], snapshot["transport_kind"]) as binary:
        hashing = _HashingRaw(binary)
        with io.TextIOWrapper(io.BufferedReader(hashing), encoding="utf-8-sig",
                              errors="strict", newline="") as text:
            reader = csv.reader(text, strict=True)
            header = next(reader, None)
            _require(header is not None, "CSV_HEADER_MISSING")
            header_json = _json(header)
            normalized = [name.strip().casefold() for name in header]
            info = {"csv_sha256": snapshot["csv_sha256"], "csv_bytes": snapshot["csv_bytes"],
                    "encoding": "utf-8-sig", "header_json": header_json,
                    "header_sha256": _sha(_frame(header)), "column_count": len(header),
                    "exact_duplicate_headers": len(header) - len(set(header)),
                    "normalized_header_collisions": len(header) - len(set(normalized)),
                    "blank_headers": normalized.count("")}
            if writing:
                fields = tuple(info) + ("row_count", "width_mismatch_rows", "records_sha256", "cells_sha256")
                values = tuple(info.values()) + (0, 0, "", "")
                cursor = connection.execute(
                    f"INSERT INTO contents ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", values)
                content_id = cursor.lastrowid
                connection.executemany("INSERT INTO headers VALUES (?,?,?)",
                                       ((content_id, i, name) for i, name in enumerate(header)))
            else:
                _require(all(saved[key] == value for key, value in info.items()), "SAVED_HEADER_MISMATCH")
                names = [(r[0], r[1]) for r in connection.execute(
                    "SELECT column_ordinal,name FROM headers WHERE content_id=? ORDER BY column_ordinal", (content_id,))]
                _require(names == list(enumerate(header)), "SAVED_HEADER_MISMATCH")
            saved_rows = None if writing else iter(connection.execute(
                "SELECT row_ordinal,cells_json,cell_count,width_matches_header,record_sha256,cells_sha256 "
                "FROM records WHERE content_id=? ORDER BY row_ordinal", (content_id,)))
            for ordinal, cells in enumerate(reader, 1):
                row_count += 1
                width_ok = int(len(cells) == len(header))
                mismatch += not width_ok
                row_frame = _frame([ordinal, cells])
                record_digest.update(row_frame)
                per_row_cells = hashlib.sha256()
                for column, value in enumerate(cells):
                    frame = _frame([ordinal, column, value])
                    cell_digest.update(frame)
                    per_row_cells.update(frame)
                record = (ordinal, _json(cells), len(cells), width_ok,
                          _sha(row_frame), per_row_cells.hexdigest())
                if writing:
                    connection.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?)", (content_id, *record))
                else:
                    actual = next(saved_rows, None)
                    _require(actual is not None and tuple(actual) == record, "SAVED_RECORD_MISMATCH")
            if not writing:
                _require(next(saved_rows, None) is None, "SAVED_RECORD_MISMATCH")
            _require(hashing.digest.hexdigest() == snapshot["csv_sha256"], "SOURCE_CHANGED_DURING_IMPORT")
    info.update(row_count=row_count, width_mismatch_rows=mismatch,
                records_sha256=record_digest.hexdigest(), cells_sha256=cell_digest.hexdigest())
    if writing:
        connection.execute("UPDATE contents SET row_count=?,width_mismatch_rows=?,records_sha256=?,cells_sha256=? "
                           "WHERE content_id=?", (row_count, mismatch, info["records_sha256"], info["cells_sha256"], content_id))
    else:
        _require(all(saved[key] == value for key, value in info.items()), "SAVED_CONTENT_FINGERPRINT_MISMATCH")
    return content_id, info


def import_csv(db_path, source_path, dataset_kind, *, member=None, output_root=None):
    """Atomically import CSV/one explicit ZIP member into a PRIVATE local DB.

    Duplicate CSV bytes share content; every successful invocation retains a
    source occurrence. An external output root must be explicitly supplied if
    the DB is not under a .wrds_private directory. No semantic adapters run.
    """
    destination = _private_destination(db_path, output_root)
    _require(isinstance(dataset_kind, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", dataset_kind)
             is not None, "INVALID_DATASET_KIND")
    source = Path(source_path).expanduser().resolve()
    _require(source.is_file(), "SOURCE_NOT_FOUND")
    _require(source != destination, "SOURCE_AND_DATABASE_MUST_DIFFER")
    connection = None
    temporary = None
    try:
        before = _snapshot_source(source, member)
        destination.parent.mkdir(parents=True, exist_ok=True)
        is_new = not destination.exists()
        if is_new:
            handle, name = tempfile.mkstemp(prefix=".wrds-import-", suffix=".sqlite", dir=destination.parent)
            os.close(handle)
            temporary = Path(name)
        connection = sqlite3.connect(temporary if is_new else destination)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        if is_new:
            _initialize(connection)
        else:
            _check_schema(connection)
        existing = connection.execute("SELECT content_id FROM contents WHERE csv_sha256=?", (before["csv_sha256"],)).fetchone()
        content_id, info = _scan_source(connection, source, before, content_id=None if existing is None else existing[0])
        after = _snapshot_source(source, before["member_name"])
        _require(before == after, "SOURCE_CHANGED_DURING_IMPORT")
        occurrence = connection.execute(
            "INSERT INTO source_occurrences (content_id,dataset_kind,source_path,transport_kind,source_sha256,source_bytes,"
            "member_name,member_crc32,imported_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
            (content_id, dataset_kind, str(source), before["transport_kind"], before["source_sha256"],
             before["source_bytes"], before["member_name"], before["member_crc32"], datetime.now(timezone.utc).isoformat()))
        occurrence_id = occurrence.lastrowid
        connection.commit()
        connection.close()
        connection = None
        if is_new:
            # Hard-link publication is atomic and cannot overwrite an existing DB.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise LocalDatabaseError("DATABASE_CREATED_CONCURRENTLY") from None
            except OSError:
                raise LocalDatabaseError("ATOMIC_DATABASE_PUBLISH_UNAVAILABLE") from None
        return {"status": "IMPORTED_RAW_REPRESENTATIONS_ONLY", "content_id": content_id,
                "occurrence_id": occurrence_id, "new_content": existing is None,
                "dataset_kind": dataset_kind, "csv_sha256": before["csv_sha256"],
                "source_sha256": before["source_sha256"], "row_count": info["row_count"],
                "column_count": info["column_count"], "width_mismatch_rows": info["width_mismatch_rows"],
                "header_diagnostics": {key: info[key] for key in (
                    "exact_duplicate_headers", "normalized_header_collisions", "blank_headers")}}
    except LocalDatabaseError:
        raise
    except (UnicodeError, csv.Error):
        raise LocalDatabaseError("CSV_PARSE_ERROR") from None
    except (zipfile.BadZipFile, RuntimeError):
        raise LocalDatabaseError("SOURCE_ARCHIVE_ERROR") from None
    except (OSError, sqlite3.DatabaseError):
        raise LocalDatabaseError("LOCAL_IMPORT_IO_OR_DATABASE_ERROR") from None
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
            Path(str(temporary) + "-journal").unlink(missing_ok=True)


def verify_import(db_path, *, content_id=None):
    """Read-only verification from retained sources, including EVERY saved cell."""
    _require(content_id is None or type(content_id) is int and content_id > 0, "INVALID_CONTENT_ID")
    connection = None
    try:
        connection = _readonly_connection(Path(db_path).expanduser().resolve())
        _require(connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "DATABASE_INTEGRITY_ERROR")
        _require(not list(connection.execute("PRAGMA foreign_key_check")), "DATABASE_FOREIGN_KEY_ERROR")
        query = "SELECT content_id FROM contents" + (" WHERE content_id=?" if content_id is not None else "") + " ORDER BY content_id"
        ids = [row[0] for row in connection.execute(query, () if content_id is None else (content_id,))]
        _require(bool(ids), "CONTENT_NOT_FOUND")
        verified_rows = occurrences = 0
        for identifier in ids:
            transports = list(connection.execute("SELECT * FROM source_occurrences WHERE content_id=? ORDER BY occurrence_id", (identifier,)))
            _require(bool(transports), "CONTENT_HAS_NO_SOURCE_OCCURRENCE")
            for i, occurrence in enumerate(transports):
                source = Path(occurrence["source_path"])
                before = _snapshot_source(source, occurrence["member_name"])
                _require(all(before[key] == occurrence[key] for key in (
                    "transport_kind", "source_sha256", "source_bytes", "member_name", "member_crc32")), "SAVED_SOURCE_IDENTITY_MISMATCH")
                expected = connection.execute("SELECT csv_sha256,csv_bytes FROM contents WHERE content_id=?", (identifier,)).fetchone()
                _require((before["csv_sha256"], before["csv_bytes"]) == tuple(expected), "SAVED_SOURCE_CONTENT_MISMATCH")
                if i == 0:
                    _, info = _scan_source(connection, source, before, content_id=identifier)
                    verified_rows += info["row_count"]
                _require(before == _snapshot_source(source, occurrence["member_name"]), "SOURCE_CHANGED_DURING_IMPORT")
                occurrences += 1
        return {"status": "VERIFIED_ALL_RECORDS_AND_CELLS_FROM_SOURCE", "verified_content_count": len(ids),
                "verified_occurrence_count": occurrences, "verified_row_count": verified_rows}
    except LocalDatabaseError:
        raise
    except (UnicodeError, csv.Error):
        raise LocalDatabaseError("CSV_PARSE_ERROR") from None
    except (OSError, sqlite3.DatabaseError, zipfile.BadZipFile, RuntimeError):
        raise LocalDatabaseError("LOCAL_VERIFY_SOURCE_OR_DATABASE_ERROR") from None
    finally:
        if connection is not None:
            connection.close()


def query_readonly(db_path, sql, parameters=(), *, limit=1000):
    """One read-only SELECT/WITH; returns positional rows to retain alias collisions."""
    _require(isinstance(sql, str) and re.match(r"\s*(?:SELECT|WITH)\b", sql, re.IGNORECASE)
             is not None, "READONLY_SELECT_OR_WITH_REQUIRED")
    _require(type(limit) is int and 0 < limit <= 10000, "INVALID_QUERY_LIMIT")
    connection = None
    try:
        connection = _readonly_connection(Path(db_path).expanduser().resolve(),
                                           require_private_schema=False)
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
        if hasattr(sqlite3, "SQLITE_RECURSIVE"):
            allowed.add(sqlite3.SQLITE_RECURSIVE)

        def authorize(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_FUNCTION and str(arg2 or arg1).casefold() in {
                    "load_extension", "readfile", "writefile", "fts3_tokenizer"}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

        connection.set_authorizer(authorize)
        cursor = connection.execute(sql, parameters)
        rows = cursor.fetchmany(limit + 1)
        return {"columns": [column[0] for column in cursor.description],
                "rows": [list(row) for row in rows[:limit]], "truncated": len(rows) > limit}
    except LocalDatabaseError:
        raise
    except (OSError, sqlite3.DatabaseError, TypeError):
        raise LocalDatabaseError("READONLY_QUERY_REJECTED") from None
    finally:
        if connection is not None:
            connection.close()
