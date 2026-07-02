# Serve Auto-Init Implementation Notes

## Slice A — 2026-07-02

### What was built

Extracted the shared indexing pipeline from `seam/cli/main.py` into a new
deep module `seam/indexer/init_index.py`, then refactored the `init` CLI
command to delegate to it. This is the load-bearing prerequisite for Slice B
(serve auto-init) so both `seam init` and `seam serve` share one code path.

**New file: `seam/indexer/init_index.py`**

- `run_init(root, *, db_dir, semantic, progress_cb) → InitResult`
- `InitResult` dataclass carrying all counters (db_path, indexed_files,
  skipped_files, total_symbols, total_edges, total_clusters, total_synthesis,
  total_test_edges, total_embeddings, llm_naming_summary)
- Pipeline: init_db → .gitignore → walk_project → per-file loop →
  index_clusters → index_synthesis → index_test_edges → (optional) index_embeddings
- progress_cb receives plain-text strings (no Rich markup) so CLI, serve, and
  tests can all consume it without stripping markup
- Never raises; optional post-passes return -1 sentinel on failure

**Modified: `seam/cli/main.py`**

- Removed 5 imports now inside init_index (init_db, walk_project,
  index_one_file, index_clusters/get_llm_naming_summary, index_synthesis,
  index_test_edges)
- Kept `connect` (used throughout main.py) and `index_embeddings` (used in
  the sync command, which has its own embed call)
- Added `from seam.indexer.init_index import InitResult, run_init`
- init command body replaced with run_init call + Progress spinner driven by
  progress_cb; summary table now uses InitResult fields instead of inlined
  counters; `len(files)` replaced by `total_files_found = indexed_files + skipped_files`

**Modified: `tests/integration/test_semantic_surfaces.py`**

- One test patched `seam.cli.main.index_embeddings`; now patches
  `seam.indexer.init_index.index_embeddings` (the new call site). Behavior
  asserted is unchanged.

**New file: `tests/unit/test_init_index.py`**

- 8 tests covering: DB creation, symbol/edge counts, idempotency, empty dir,
  non-git dir, db_dir override, progress_cb invocation, .gitignore written

### Key decisions

1. **Separate db_dir type from CLI string**: `run_init` takes `Path | None`
   for db_dir; the CLI converts its `str` arg to `Path.resolve()` or `None`
   before calling. This keeps init_index.py free of CLI-layer string-handling.

2. **total_files_found from indexed + skipped**: the original code used
   `len(files)` (from the local `files = walk_project(...)` call). After
   extracting, walk_project is inside run_init. `InitResult.indexed_files +
   InitResult.skipped_files` is the correct equivalent because every file is
   either indexed or skipped — no third category.

3. **index_embeddings kept in main.py imports**: the sync command calls
   `index_embeddings` directly (it has its own open-connection flow). Removing
   it from main.py would break that. Only the init-specific imports were moved.

4. **Progress callback is plain text**: Rich markup in the callback string
   would pollute the serve/non-CLI callers. The CLI's spinner sees plain text
   and does its own formatting via Rich.

5. **Test patch target moved**: `patch("seam.indexer.init_index.index_embeddings")`
   is the correct site because that's where the name is bound and called. The
   old `seam.cli.main.index_embeddings` import was removed so the old patch
   would silently do nothing.

### Deviations from PRD

None. Slice A exactly matches the PRD §"Extract a deep indexing module" and
§"init command delegates to the shared function". Output byte-stability was
verified: all 3175 pre-existing tests pass (+ 8 new ones).

### Open questions / Slice B notes

- `serve.py` is ready to call `run_init` when the DB is missing (Slice B).
- The `[web]` check must happen BEFORE `run_init` to avoid indexing then failing.
- Progress display in serve context: a simple `console.print` per callback line
  is sufficient (no spinner needed — the process exits after serving starts).
