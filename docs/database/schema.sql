-- Seam SQLite Schema (v2 — Phase 1 Core)
-- Run via db.py:init_db() — idempotent (CREATE TABLE IF NOT EXISTS).
-- FTS5 is required; init_db() verifies availability before proceeding.
-- Schema v2 adds: edges.confidence (DEFAULT 'INFERRED').
-- Migration from v1: db.py:_run_migration_v1_to_v2() (guarded ALTER TABLE).

PRAGMA journal_mode = WAL;      -- Write-ahead logging for concurrent reads
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;    -- Faster than FULL; safe with WAL

-- ── Files ────────────────────────────────────────────────────────────────────
-- One row per indexed source file. Used to detect stale files on re-index.
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,   -- Absolute path (resolved at index time)
    language    TEXT NOT NULL,          -- 'python' | 'typescript' | 'javascript'
    file_hash   TEXT NOT NULL,          -- SHA1 of file content (for change detection)
    mtime       REAL NOT NULL,          -- os.stat().st_mtime at index time
    indexed_at  REAL NOT NULL           -- time.time() when this file was indexed
);

CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);

-- ── Symbols ──────────────────────────────────────────────────────────────────
-- One row per extracted symbol (function, class, method, interface, etc.)
CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,          -- Fully-qualified when possible (Class.method)
    kind        TEXT NOT NULL,          -- 'function' | 'class' | 'method' | 'interface' | 'type'
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    docstring   TEXT                    -- First docstring/JSDoc block if present; NULL otherwise
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file_id ON symbols(file_id);

-- ── Edges ────────────────────────────────────────────────────────────────────
-- Directed relationships between symbols.
-- source_name / target_name store the string name (not ID) so edges survive
-- re-indexing of either endpoint independently.
-- confidence: EXTRACTED (resolved to 1 symbol) | INFERRED (heuristic) | AMBIGUOUS (name collision)
CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,          -- Symbol name of the caller/importer
    target_name TEXT NOT NULL,          -- Symbol name of the callee/importee
    kind        TEXT NOT NULL,          -- 'import' | 'call'
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    line        INTEGER NOT NULL,       -- Line where the relationship is expressed
    confidence  TEXT NOT NULL DEFAULT 'INFERRED'   -- EXTRACTED | INFERRED | AMBIGUOUS (DEFAULT is INFERRED: conservative)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_name);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_name);

-- ── FTS5 (Full-Text Search) ───────────────────────────────────────────────────
-- Virtual table for BM25-ranked search across symbol names + docstrings.
-- Kept in sync with 'symbols' via triggers below.
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name,
    docstring,
    content='symbols',
    content_rowid='id'
);

-- Triggers: keep FTS5 in sync with symbols (avoids manual sync in application code)
CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, docstring)
    VALUES (new.id, new.name, new.docstring);
END;

CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring)
    VALUES ('delete', old.id, old.name, old.docstring);
END;

CREATE TRIGGER IF NOT EXISTS symbols_au AFTER UPDATE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring)
    VALUES ('delete', old.id, old.name, old.docstring);
    INSERT INTO symbols_fts(rowid, name, docstring)
    VALUES (new.id, new.name, new.docstring);
END;

-- ── Metadata ─────────────────────────────────────────────────────────────────
-- Key-value store for index metadata (version, created_at, etc.)
CREATE TABLE IF NOT EXISTS metadata (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

INSERT OR IGNORE INTO metadata(key, value) VALUES
    ('schema_version', '2'),
    ('seam_version',   '0.1.0');
