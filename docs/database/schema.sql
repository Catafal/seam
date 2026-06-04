-- Seam SQLite Schema (v7 — Semantic search foundation)
-- Run via db.py:init_db() — idempotent (CREATE TABLE IF NOT EXISTS).
-- FTS5 is required; init_db() verifies availability before proceeding.
-- Schema v2 adds: edges.confidence (DEFAULT 'INFERRED').
-- Schema v3 adds: comments table (WHY/HACK/NOTE/TODO/FIXME semantic comments).
-- Schema v4 adds: clusters table + symbols.cluster_id (graph community detection).
-- Schema v5 adds: symbols.signature, decorators, is_exported, visibility, qualified_name;
--                 FTS5 rebuilt to index (name, docstring, signature).
-- Schema v6 adds: import_mappings table (Phase 5 import resolution).
-- Schema v7 adds: embeddings table (semantic search via local fastembed vectors).
-- Migration from v1: db.py:_run_migration_v1_to_v2() (guarded ALTER TABLE).
-- Migration from v2: db.py:_run_migration_v2_to_v3() (guards schema_version bump).
-- Migration from v3: db.py:_run_migration_v3_to_v4() (adds clusters table + cluster_id).
-- Migration from v4: db.py:_run_migration_v4_to_v5() (adds 5 enrichment cols + FTS rebuild).
-- Migration from v5: db.py:_run_migration_v5_to_v6() (adds import_mappings table).
-- Migration from v6: db.py:_run_migration_v6_to_v7() (adds embeddings table).

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

-- ── Import Mappings ──────────────────────────────────────────────────────────
-- One row per import binding extracted from a source file during indexing.
-- Populated by pipeline.py (index_one_file) alongside symbols/edges.
-- Refreshed per-file by the watcher (delete-then-insert, same as upsert_file).
-- NOT backfilled by migration — only a full `seam init` populates these rows.
-- Schema v6 addition (Phase 5: import resolution confidence promotion).
CREATE TABLE IF NOT EXISTS import_mappings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    local_name    TEXT NOT NULL,   -- name used in the referencing file (alias or original)
    exported_name TEXT NOT NULL,   -- original name in the source module
    source_module TEXT NOT NULL,   -- import source as written (e.g. 'app.parser', './utils')
    is_default    INTEGER NOT NULL DEFAULT 0,   -- 1 for default imports (import X from ...)
    is_namespace  INTEGER NOT NULL DEFAULT 0,   -- 1 for namespace imports (import * as ns)
    is_wildcard   INTEGER NOT NULL DEFAULT 0,   -- 1 for wildcard imports (from x import *)
    line          INTEGER NOT NULL              -- 1-based line number of the import statement
);

-- Index for fast lookup by file (primary access pattern: load all mappings for a file)
CREATE INDEX IF NOT EXISTS idx_import_mappings_file_id ON import_mappings(file_id);
-- Index for fast lookup by local name (used in import-promotion step A)
CREATE INDEX IF NOT EXISTS idx_import_mappings_local_name ON import_mappings(local_name);

-- ── Embeddings ───────────────────────────────────────────────────────────────
-- One row per symbol that has been embedded with a local fastembed model.
-- Populated ONLY by `seam init --semantic` (NOT by the base `seam init`).
-- Not backfilled by migration — embeddings stay absent until a --semantic run.
-- When absent (or model mismatch), seam_search/seam_query degrade to FTS5-only.
-- Schema v7 addition (semantic search foundation).
CREATE TABLE IF NOT EXISTS embeddings (
    symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    model     TEXT NOT NULL,    -- Model name used to produce this vector (e.g. 'BAAI/bge-small-en-v1.5')
    dim       INTEGER NOT NULL, -- Vector dimensionality (e.g. 384 for bge-small)
    vector    BLOB NOT NULL     -- float32 bytes: numpy.array(..., dtype=np.float32).tobytes()
);

-- ── Metadata ─────────────────────────────────────────────────────────────────
-- Key-value store for index metadata (version, created_at, etc.)
CREATE TABLE IF NOT EXISTS metadata (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

-- NOTE: INSERT OR IGNORE does not update existing rows. Fresh DBs are seeded at
-- the CURRENT schema version ('7') so a brand-new `seam init` is born current and
-- does NOT trigger any migration advisory.
-- Existing older DBs keep their stored version; db.py migrations bump them in place.
INSERT OR IGNORE INTO metadata(key, value) VALUES
    ('schema_version', '7'),
    ('seam_version',   '0.2.0');
