-- LOCAL LICENSED ROW STORE ONLY. DO NOT POPULATE IN PUBLIC DIRECTORY.
CREATE TABLE local_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE contents (
        content_id INTEGER PRIMARY KEY, csv_sha256 TEXT NOT NULL UNIQUE,
        csv_bytes INTEGER NOT NULL, encoding TEXT NOT NULL,
        header_json TEXT NOT NULL, header_sha256 TEXT NOT NULL,
        column_count INTEGER NOT NULL, exact_duplicate_headers INTEGER NOT NULL,
        normalized_header_collisions INTEGER NOT NULL, blank_headers INTEGER NOT NULL,
        row_count INTEGER NOT NULL, width_mismatch_rows INTEGER NOT NULL,
        records_sha256 TEXT NOT NULL, cells_sha256 TEXT NOT NULL);
CREATE TABLE headers (
        content_id INTEGER NOT NULL REFERENCES contents(content_id),
        column_ordinal INTEGER NOT NULL, name TEXT NOT NULL,
        PRIMARY KEY(content_id, column_ordinal));
CREATE TABLE records (
        content_id INTEGER NOT NULL REFERENCES contents(content_id),
        row_ordinal INTEGER NOT NULL, cells_json TEXT NOT NULL,
        cell_count INTEGER NOT NULL, width_matches_header INTEGER NOT NULL,
        record_sha256 TEXT NOT NULL, cells_sha256 TEXT NOT NULL,
        PRIMARY KEY(content_id, row_ordinal));
CREATE TABLE source_occurrences (
        occurrence_id INTEGER PRIMARY KEY,
        content_id INTEGER NOT NULL REFERENCES contents(content_id),
        dataset_kind TEXT NOT NULL, source_path TEXT NOT NULL,
        transport_kind TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        source_bytes INTEGER NOT NULL, member_name TEXT, member_crc32 TEXT,
        imported_at_utc TEXT NOT NULL);
