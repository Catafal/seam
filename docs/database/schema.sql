-- Seam SQLite Schema (v16 — document grounding)
-- Run via db.py:init_db() — idempotent (CREATE TABLE IF NOT EXISTS).
-- FTS5 is required; init_db() verifies availability before proceeding.
-- Schema v2 adds: edges.confidence (DEFAULT 'INFERRED').
-- Schema v3 adds: comments table (WHY/HACK/NOTE/TODO/FIXME semantic comments).
-- Schema v4 adds: clusters table + symbols.cluster_id (graph community detection).
-- Schema v5 adds: symbols.signature, decorators, is_exported, visibility, qualified_name;
--                 FTS5 rebuilt to index (name, docstring, signature).
-- Schema v6 adds: import_mappings table (Phase 5 import resolution).
-- Schema v7 adds: embeddings table (semantic search via local fastembed vectors).
-- Schema v8 adds: clusters.cohesion (P2 internal-edge ratio; small search bonus).
-- Schema v9 adds: symbols.entry_score (P6b framework entry-point ranking multiplier).
-- Schema v10 adds: edges.receiver (Tier B B1: raw receiver text for attribute calls;
--                  NULL for import/bare-call edges and pre-v10 rows).
-- Schema v11 adds: symbols.search_text (Tier D #12: camelCase/snake_case-split tokens;
--                  FTS5 rebuilt to index (name, docstring, signature, search_text)).
-- Schema v12 adds: edges.synthesized_by (edge-synthesis post-pass provenance;
--                  NULL = parser-extracted; channel name = synthesized by post-pass).
-- Schema v13 adds: routes table for first-class route node metadata.
-- Schema v14 adds: config_keys and resources tables for no-secret config/resource metadata.
-- Schema v15 adds: edges.provenance for direct extractor evidence channels.
-- Schema v16 adds: document_files, document_anchors, and document_references
--                  for local docs/spec grounding evidence.
-- Migration from v1: db.py:_run_migration_v1_to_v2() (guarded ALTER TABLE).
-- Migration from v2: db.py:_run_migration_v2_to_v3() (guards schema_version bump).
-- Migration from v3: db.py:_run_migration_v3_to_v4() (adds clusters table + cluster_id).
-- Migration from v4: db.py:_run_migration_v4_to_v5() (adds 5 enrichment cols + FTS rebuild).
-- Migration from v5: db.py:_run_migration_v5_to_v6() (adds import_mappings table).
-- Migration from v6: db.py:_run_migration_v6_to_v7() (adds embeddings table).
-- Migration from v7: db.py:_run_migration_v7_to_v8() (adds clusters.cohesion column).
-- Migration from v8: db.py:_run_migration_v8_to_v9() (adds symbols.entry_score column).
-- Migration from v9: db.py:_run_migration_v9_to_v10() (adds edges.receiver column).
-- Migration from v10: db.py:_run_migration_v10_to_v11() (adds symbols.search_text + FTS column).
-- Migration from v11: db.py:_run_migration_v11_to_v12() (adds edges.synthesized_by column).
-- Migration from v12: db.py:_run_migration_v12_to_v13() (adds routes table).
-- Migration from v13: db.py:_run_migration_v13_to_v14() (adds config/resource tables).
-- Migration from v14: db.py:_run_migration_v14_to_v15() (adds edges.provenance column).
-- Migration from v15: db.py:_run_migration_v15_to_v16() (adds document grounding tables).

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
    kind           TEXT NOT NULL,          -- 'function' | 'class' | 'method' | 'interface' | 'type' | 'field' [A3]
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    docstring      TEXT,                   -- First docstring/JSDoc block if present; NULL otherwise
    cluster_id     INTEGER,                -- FK to clusters.id; NULL until clustering post-pass runs
    -- Phase 4 node-enrichment fields (all nullable; NULL when not extracted or pre-v5 rows)
    signature      TEXT,                   -- Declaration header, one line, truncated to SEAM_MAX_SIGNATURE_LEN
    decorators     TEXT,                   -- JSON-encoded list of decorator strings; '[]' for empty
    is_exported    INTEGER,                -- 1=exported/public, 0=unexported/private, NULL=unknown
    visibility     TEXT,                   -- 'public' | 'private' | 'protected' | 'crate' | NULL
    qualified_name TEXT,                   -- 'ClassName.method' or plain name; NULL when unknown
    -- P6b (v9): framework entry-point ranking multiplier (>=1.0). Computed at index
    -- time from the file path pattern + decorator text (processes.compute_entry_score).
    -- NULL on pre-v9 rows until re-index; list_entry_points treats NULL as baseline 1.0.
    entry_score    REAL,
    -- Tier D #12 (v11): camelCase/snake_case-split tokens of name + qualified_name, space-
    -- joined and deduped (tokenize.build_search_text). Indexed as a 4th symbols_fts column so
    -- "push to talk monitor" matches GlobalPushToTalkShortcutMonitor. NULL on pre-v11 rows and
    -- when SEAM_TOKENIZE_IDENTIFIERS=off → no split-token recall until re-index.
    search_text    TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file_id ON symbols(file_id);

-- ── Edges ────────────────────────────────────────────────────────────────────
-- Directed relationships between symbols.
-- source_name / target_name store the string name (not ID) so edges survive
-- re-indexing of either endpoint independently.
-- confidence: EXTRACTED (resolved to 1 symbol) | INFERRED (heuristic) | AMBIGUOUS (name collision)
-- receiver: raw receiver expression text for attribute calls (e.g., 'self', 'obj').
--   NULL for import edges, bare-identifier call edges, and pre-v10 rows.
--   Captured at extraction time to enable later receiver-type inference (Tier B).
CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,          -- Symbol name of the caller/importer
    target_name TEXT NOT NULL,          -- Symbol name of the callee/importee
    kind        TEXT NOT NULL,          -- 'import' | 'call' | 'extends' | 'implements' | 'instantiates' | 'holds' | 'reads' | 'writes' | 'uses' | 'http_calls' | 'reads_config' | 'configures' | 'raises' | 'catches' | 'tests'
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    line        INTEGER NOT NULL,       -- Line where the relationship is expressed
    confidence  TEXT NOT NULL DEFAULT 'INFERRED',  -- EXTRACTED | INFERRED | AMBIGUOUS (DEFAULT is INFERRED: conservative)
    -- Tier B B1 (v10): receiver text from attribute call expressions (recv.method).
    -- NULL for import edges, bare calls, and pre-v10 rows (null-contract: same as Phase 4/5 fields).
    receiver    TEXT,
    -- v12+: post-pass provenance for derived edges.
    -- NULL = parser/direct extractor edge.
    -- Non-NULL = derived by a named post-pass channel, e.g. 'interface-override',
    -- 'test-call', 'test-import', or 'test-name-proximity'.
    -- Do not interpret non-NULL as runtime coverage or as always heuristic: direct
    -- test-call evidence is static evidence derived after the whole index is known.
    -- Some synthesis channels use synthetic file rows; test-edge channels use the
    -- test file where the evidence was observed.
    synthesized_by TEXT,
    -- v15+: direct-extractor provenance detail. NULL for ordinary parser edges;
    -- non-NULL for parser/direct evidence where the extraction channel matters
    -- without implying a post-pass synthesized edge.
    provenance TEXT
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_name);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_name);

-- ── FTS5 (Full-Text Search) ───────────────────────────────────────────────────
-- Virtual table for BM25-ranked search across symbol names + docstrings + signatures
-- + search_text (Tier D #12 split tokens). Read-path bm25() weights search_text lowest.
-- Phase 5 change: added 'signature' column so type-shaped queries match on params/returns.
-- Kept in sync with 'symbols' via triggers below.
-- Tier D #12 (v11): search_text column added — split identifier tokens for camelCase recall.
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name,
    docstring,
    signature,
    search_text,
    content='symbols',
    content_rowid='id'
);

-- Triggers: keep FTS5 in sync with symbols (avoids manual sync in application code)
CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, docstring, signature, search_text)
    VALUES (new.id, new.name, new.docstring, new.signature, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring, signature, search_text)
    VALUES ('delete', old.id, old.name, old.docstring, old.signature, old.search_text);
END;

CREATE TRIGGER IF NOT EXISTS symbols_au AFTER UPDATE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, docstring, signature, search_text)
    VALUES ('delete', old.id, old.name, old.docstring, old.signature, old.search_text);
    INSERT INTO symbols_fts(rowid, name, docstring, signature, search_text)
    VALUES (new.id, new.name, new.docstring, new.signature, new.search_text);
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
    naming_source TEXT NOT NULL,        -- 'deterministic' | 'llm'
    -- P2 (v8): internal-edge ratio in [0,1]. NULL on pre-v8 rows until re-index.
    -- Higher = tighter, more self-contained community. Small additive search bonus.
    cohesion      REAL
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

-- ── Routes ───────────────────────────────────────────────────────────────────
-- One row per first-class HTTP route symbol emitted during indexing.
-- Route nodes live in symbols(kind='route') so existing graph surfaces can discover
-- them. HTTP-specific fields stay here so generic symbols are not widened with
-- route-only concepts.
CREATE TABLE IF NOT EXISTS routes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    symbol_name     TEXT NOT NULL,
    method          TEXT NOT NULL,
    path            TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    framework       TEXT NOT NULL,
    handler         TEXT,
    line            INTEGER NOT NULL,
    confidence      TEXT NOT NULL DEFAULT 'INFERRED',
    provenance      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_routes_file_id ON routes(file_id);
CREATE INDEX IF NOT EXISTS idx_routes_method_path ON routes(method, normalized_path);
CREATE INDEX IF NOT EXISTS idx_routes_symbol_name ON routes(symbol_name);

-- ── Config / Resources ───────────────────────────────────────────────────────
-- One row per config-key evidence item emitted during indexing.
-- Config nodes live in symbols(kind='config') so graph surfaces can discover them.
-- Raw values are intentionally NOT stored; value_state/value_category preserve only
-- a redacted safety shape so graph/search/MCP payloads cannot leak secrets.
CREATE TABLE IF NOT EXISTS config_keys (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id        INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    symbol_name    TEXT NOT NULL,
    key            TEXT NOT NULL,
    normalized_key TEXT NOT NULL,
    source_family  TEXT NOT NULL,
    role           TEXT NOT NULL, -- declaration | read
    value_state    TEXT NOT NULL,
    value_category TEXT,
    line           INTEGER NOT NULL,
    confidence     TEXT NOT NULL DEFAULT 'INFERRED',
    provenance     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_config_keys_file_id ON config_keys(file_id);
CREATE INDEX IF NOT EXISTS idx_config_keys_normalized_key ON config_keys(normalized_key);
CREATE INDEX IF NOT EXISTS idx_config_keys_symbol_name ON config_keys(symbol_name);

-- One row per runtime-resource evidence item emitted during indexing.
-- Resource nodes live in symbols(kind='resource'); resource-specific category and
-- provenance live here to avoid widening the generic symbol contract.
CREATE TABLE IF NOT EXISTS resources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    symbol_name     TEXT NOT NULL,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    category        TEXT NOT NULL,
    source_family   TEXT NOT NULL,
    line            INTEGER NOT NULL,
    confidence      TEXT NOT NULL DEFAULT 'INFERRED',
    provenance      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resources_file_id ON resources(file_id);
CREATE INDEX IF NOT EXISTS idx_resources_category ON resources(category);
CREATE INDEX IF NOT EXISTS idx_resources_symbol_name ON resources(symbol_name);

-- ── Document grounding ──────────────────────────────────────────────────────
-- Local docs/spec evidence is intentionally separate from dependency edges.
-- Document anchors can ground code intent, but they are never code reachability.
CREATE TABLE IF NOT EXISTS document_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    doc_kind    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'unknown',
    title       TEXT,
    fingerprint TEXT NOT NULL,
    indexed_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_files_kind ON document_files(doc_kind);
CREATE INDEX IF NOT EXISTS idx_document_files_status ON document_files(status);
CREATE INDEX IF NOT EXISTS idx_document_files_path ON document_files(path);

CREATE TABLE IF NOT EXISTS document_anchors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES document_files(id) ON DELETE CASCADE,
    heading_path TEXT NOT NULL,
    slug         TEXT NOT NULL,
    anchor_type  TEXT NOT NULL,
    start_line   INTEGER NOT NULL,
    end_line     INTEGER NOT NULL,
    search_text  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_anchors_document ON document_anchors(document_id);
CREATE INDEX IF NOT EXISTS idx_document_anchors_slug ON document_anchors(slug);

CREATE TABLE IF NOT EXISTS document_references (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    anchor_id      INTEGER NOT NULL REFERENCES document_anchors(id) ON DELETE CASCADE,
    target_kind    TEXT NOT NULL,
    target_value   TEXT NOT NULL,
    resolved_kind  TEXT,
    resolved_value TEXT,
    relation_type  TEXT NOT NULL,
    confidence     TEXT NOT NULL,
    line           INTEGER NOT NULL,
    provenance     TEXT NOT NULL,
    caveat         TEXT
);

CREATE INDEX IF NOT EXISTS idx_document_references_anchor ON document_references(anchor_id);
CREATE INDEX IF NOT EXISTS idx_document_references_target ON document_references(target_kind, target_value);
CREATE INDEX IF NOT EXISTS idx_document_references_resolved ON document_references(resolved_kind, resolved_value);
CREATE INDEX IF NOT EXISTS idx_document_references_relation ON document_references(relation_type);
CREATE INDEX IF NOT EXISTS idx_document_references_confidence ON document_references(confidence);

-- ── Metadata ─────────────────────────────────────────────────────────────────
-- Key-value store for index metadata (version, created_at, etc.)
CREATE TABLE IF NOT EXISTS metadata (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

-- NOTE: INSERT OR IGNORE does not update existing rows. Fresh DBs are seeded at
-- the CURRENT schema version ('16') so a brand-new `seam init` is born current and
-- does NOT trigger any migration advisory.
-- Existing older DBs keep their stored version; db.py migrations bump them in place.
INSERT OR IGNORE INTO metadata(key, value) VALUES
    ('schema_version', '16'),
    ('seam_version',   '0.2.0');
