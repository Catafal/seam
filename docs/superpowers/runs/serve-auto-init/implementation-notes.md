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

## Slice B — 2026-07-02

### What was built

Modified `seam/cli/serve.py` to auto-init on a missing index and added a
`--no-init` flag to preserve the old explicit-error behavior for scripting/CI.

**Modified: `seam/cli/serve.py`**

- Added `from seam.indexer.init_index import run_init` at module top level
  (always-available, not an optional extra — top-level import is correct per CLAUDE.md).
- Added `no_init: bool = typer.Option(False, "--no-init", ...)` parameter.
- Extracted `_ensure_index(project_root, db_path, no_init) → None` helper to
  keep `serve_command` under 200 lines and to make the auto-init logic monkeypatch-able.
- **Reordered guards**: `[web]` availability is checked FIRST (call
  `_load_web_app_factory()` up front), then `_ensure_index` runs. This
  guarantees the user never waits for a full index only to hit a missing-dep error.
- `_ensure_index` behavior:
  - db_path exists → no-op (never re-init a present-but-stale index)
  - missing + `--no-init` → print "No index found. Run seam init first." + Exit(1)
  - missing + default → print one-time message, call `run_init`, on success
    print file/symbol counts and continue; on exception print clear error + Exit(1)

**Modified: `tests/integration/test_serve_cli.py`**

- Updated T1 (`test_serve_missing_extra_exits_with_hint`): removed the
  unnecessary `_make_indexed_repo` setup — since [web] is checked FIRST, an
  indexed repo is no longer required to reach the factory call.
- Updated T2 (`test_serve_no_index_exits_with_message`) → renamed to
  `test_serve_no_init_flag_missing_index_exits`: added `--no-init` to the
  invocation (the old "exit on missing index" is now only triggered by `--no-init`).
- Updated T3 (`test_serve_default_options_are_valid`): added `--no-init` to
  prevent auto-init from triggering during the parse-only check.
- Added 4 new tests (T4–T7):
  - T4: missing index + default → run_init invoked, db created, uvicorn.run called.
  - T5: existing index → run_init NOT called, uvicorn.run called.
  - T6: missing [web] → error before run_init (no db created).
  - T7: run_init raises → clear error + Exit(1), uvicorn.run NOT called.

### Key decisions

1. **[web] check before auto-init**: the most important ordering decision. If
   we indexed first, the user could wait minutes on a large repo and then get
   a "pip install seam-code[web]" error. Checking [web] first (cheap import
   probe) surfaces the dependency error immediately.

2. **`_ensure_index` extracted as a private helper**: `serve_command` stayed
   comfortably under 200 lines with the helper. It also makes the auto-init
   path independently monkeypatchable in tests without touching uvicorn or
   the FastAPI factory.

3. **`run_init` imported at top level**: it's not an optional extra — it's
   always available. CLAUDE.md mandates top-level imports for always-available
   modules. This also makes monkeypatching straightforward (`setattr(serve_mod,
   "run_init", mock)` works because the name is bound at module level).

4. **Defensive try/except around run_init**: run_init's contract says "never
   raises", but serve.py wraps the call in a try/except anyway. The serve
   context is user-facing (one-command onboarding) and a surprise traceback
   on a contract violation is far worse than the extra guard.

5. **Post-auto-init db_path re-resolve**: after `_ensure_index`, `db_path` is
   re-read from `config.get_db_path(project_root)`. Currently run_init always
   places the DB at the same location as the initial `db_path`, so this is
   defensive — but it keeps the code correct if a future `db_dir` override
   is threaded through.

6. **Tests use sys.modules patching for uvicorn**: since `import uvicorn` is
   a local statement inside `serve_command`, standard attribute monkeypatching
   doesn't work. `monkeypatch.setitem(sys.modules, "uvicorn", fake_module)`
   intercepts the lazy import transparently.

7. **Behavioral assertions over call-sequence assertions**: T4 checks that the
   db was created and uvicorn.run was reached — external behaviors. It does NOT
   assert the internal call order. T5 checks that run_init was NOT called (the
   important correctness property for the existing-index path).

### Deviations from PRD

None. Slice B exactly matches the PRD §"seam serve auto-inits on a missing index"
and §"Testing Decisions". Final gate: 3179 passed, 6 skipped (real-model tests),
ruff clean, mypy clean.
