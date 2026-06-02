-- Seam SQLite Schema (v5 — Phase 4: node-field enrichment)
-- Run via db.py:init_db() — idempotent (CREATE TABLE IF NOT EXISTS).
-- FTS5 is required; init_db() verifies availability before proceeding.
-- Schema v2 adds: edges.confidence (DEFAULT 'INFERRED').
-- Schema v3 adds: comments table (WHY/HACK/NOTE/TODO/FIXME semantic comments).
-- Schema v4 adds: clusters table + symbols.cluster_id (graph community detection).
-- Schema v5 adds: symbols.signature, decorators, is_exported, visibility, qualified_name;
--                 FTS5 rebuilt to index (name, docstring, signature).
-- Migration from v1: db.py:_run_migration_v1_to_v2() (guarded ALTER TABLE).
-- Migration from v2: db.py:_run_migration_v2_to_v3() (guards schema_version bump).
-- Migration from v3: db.py:_run_migration_v3_to_v4() (adds clusters table + cluster_id).
-- Migration from v4: db.py:_run_migration_v4_to_v5() (adds 5 enrichment cols + FTS rebuild).

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
-- Phase 4 adds: signature, decorators, is_exported, visibility, qualified_name.
CREATE TABLE IF NOT EXISTS symbols (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id        INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,          -- Fully-qualified when possible (Class.method)
    kind           TEXT NOT NULL,          -- 'function' | 'class' | 'method' | 'interface' | 'type'
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    docstring      TEXT,                   -- First docstring/JSDoc block if present; NULL otherwise
    cluster_id     INTEGER,                -- FK to clusters.id; NULL until clustering post-pass runs
    -- Phase 4 node-enrichment fields (all nullable; NULL when not extracted or pre-v5 rows)
    signature      TEXT,                   -- Declaration header, one line, truncated to SEAM_MAX_SIGNATURE_LEN
    decorators     TEXT,                   -- JSON-encoded list of decorator strings; '[]' for empty
    is_exported    INTEGER,                -- 1=exported/public, 0=unexported/private, NULL=unknown
    visibility     TEXT,                   -- 'public' | 'private' | 'protected' | 'crate' | NULL
    qualified_name TEXT                    -- 'ClassName.method' or plain name; NULL when unknown
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
-- Virtual table for BM25-ranked search across symbol names + docstrings + signatures.
-- Phase 5 change: added 'signature' column so type-shaped queries match on params/returns.
-- Kept in sync with 'symbols' via triggers below.
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name,
    docstring,
    signature,
    content='symbols',
    content_rowid='id'
);

-- Triggers: keep FTS5 in sync with symbols (avoids manual sync in application code)
CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, docstring, signature)
    VALUES (new.id, new.name, new.docstring, new.signature);
END;

CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring, signature)
    VALUES ('delete', old.id, old.name, old.docstring, old.signature);
END;

CREATE TRIGGER IF NOT EXISTS symbols_au AFTER UPDATE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring, signature)
    VALUES ('delete', old.id, old.name, old.docstring, old.signature);
    INSERT INTO symbols_fts(rowid, name, docstring, signature)
    VALUES (new.id, new.name, new.docstring, new.signature);
END;

-- ── Comments ─────────────────────────────────────────────────────────────────
-- Semantic comments extracted during indexing: WHY/HACK/NOTE/TODO/FIXME markers.
-- Cascade-deleted when the parent file is removed or re-indexed.
-- No FTS: lookup is by file_id + line range (proximity), not full-text.
CREATE TABLE IF NOT EXISTS comments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    line    INTEGER NOT NULL,           -- 1-based line number in the source file
    marker  TEXT NOT NULL,              -- Normalized UPPERCASE: WHY|HACK|NOTE|TODO|FIXME
    text    TEXT NOT NULL               -- Comment body after the marker (and optional colon), stripped
);

-- Index to speed up file-scoped lookups (the dominant query pattern).
CREATE INDEX IF NOT EXISTS idx_comments_file_id ON comments(file_id);

-- ── Clusters ─────────────────────────────────────────────────────────────────
-- One row per community detected during `seam init` clustering post-pass.
-- Populated after the full indexing loop, not per-file. Cleared and repopulated
-- on each full `seam init`. Watcher does NOT recompute clusters.
CREATE TABLE IF NOT EXISTS clusters (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label         TEXT NOT NULL,        -- Human-readable label (deterministic or LLM-generated)
    size          INTEGER NOT NULL,     -- Number of member symbols
    naming_source TEXT NOT NULL         -- 'deterministic' | 'llm'
);

CREATE INDEX IF NOT EXISTS idx_clusters_id ON clusters(id);

-- ── Metadata ─────────────────────────────────────────────────────────────────
-- Key-value store for index metadata (version, created_at, etc.)
CREATE TABLE IF NOT EXISTS metadata (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

-- NOTE: INSERT OR IGNORE does not update existing rows. Fresh DBs are seeded at
-- the CURRENT schema version ('5') so a brand-new `seam init` is born current and
-- does NOT trigger any migration advisory.
-- Existing older DBs keep their stored version; db.py migrations bump them in place.
INSERT OR IGNORE INTO metadata(key, value) VALUES
    ('schema_version', '5'),
    ('seam_version',   '0.2.0');
