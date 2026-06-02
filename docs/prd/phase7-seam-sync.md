# Phase 7 — One-Shot `seam sync` with Gated Full Cluster Recompute

> Depth tier, roadmap `.claude/research/codegraph-vs-seam.md` §8 item 7 / §6.1 / §6.5a.
> Builds on the existing indexing path: `pipeline.walk_project`, `pipeline.index_one_file`,
> `db.delete_file`, `cluster_index.index_clusters`, and the `files` table's existing
> `mtime` + `file_hash` columns. **No schema change.**

## Problem Statement

As a developer (or an AI agent acting on my behalf), after I edit, add, or delete a handful of
source files I have only two ways to refresh the Seam index, and both are unsatisfying:

1. **Re-run `seam init`** — correct but wasteful: it re-parses and re-upserts *every* file in the
   repo even though I touched three of them. On a large repo that is slow enough that I avoid
   running it, so my index drifts.
2. **Rely on the live `seam start` watcher** — but the watcher only runs while I keep a daemon
   alive, and it has a documented blind spot: **it never recomputes clusters after per-file
   edits.** New symbols added after the last `seam init` carry `cluster_id=NULL`, and existing
   `seam_clusters` results go stale, until I remember to run a full `seam init` again.

What I actually want is a **one-shot reconcile**: "look at what changed on disk since the last
index, re-index only those files, drop files that were deleted, and refresh the clusters — but
only if anything actually changed." It must be cheap enough to wire into a git `post-merge` /
`post-checkout` / pre-push hook, and quiet enough to run as `( seam sync -q >/dev/null 2>&1 & )`.

## Solution

A new CLI command **`seam sync`** backed by a new deep module `seam/indexer/sync.py`. It performs
a **filesystem reconcile** against the existing index and then a **full cluster recompute gated on
whether the graph actually changed**:

1. **Detect** — walk the project tree (reusing `walk_project`), compare each file against the
   `files` table. A file is *unchanged* if its on-disk `st_mtime` matches the stored `mtime`
   (cheap pre-filter, no read); otherwise its content is hashed (SHA-1, reusing `pipeline.sha1`)
   and compared to the stored `file_hash` — re-indexed only if the hash differs. Tracked files no
   longer present on disk are deleted. This is **filesystem reconcile, never git** — it catches
   non-git repos *and* committed changes pulled in by merge/checkout/rebase.
2. **Re-index incrementally** — only added + content-changed files go through `index_one_file`;
   removed files go through `delete_file`. Unchanged files are skipped without a re-parse.
3. **Recompute clusters, gated** — Seam's clusters are **global** (Louvain over the whole
   name-keyed graph; one edge can re-partition unrelated communities), so there is no cheap
   *incremental* cluster update. Instead, after reconcile, run a **full** `index_clusters` pass —
   but **only when the graph changed** (`added + modified + removed > 0`). When nothing changed,
   the cluster pass is skipped entirely, mirroring CodeGraph's "skip maintenance when the changed
   set is empty" gate. This directly kills Seam's documented "clusters go stale after edits"
   gotcha for the `seam sync` path.

A **`--force-clusters`** escape hatch recomputes clusters even when zero files changed — the
honest fix for the case where the *live watcher* already indexed my edits into the `files` table
(so `seam sync` sees no on-disk drift) but left the clusters stale.

The command surfaces the reconcile outcome three ways, consistent with the rest of the CLI:
- **default** — a Rich summary table (added / modified / removed / unchanged / skipped / clusters);
- **`--quiet` / `-q`** — bare values, one per line, for hook use;
- **`--json`** — the `{ok:true, data:{…}}` structured envelope for CI / agents to branch on.

`seam sync` is a **maintenance command** (like `init` / `start` / `status`): CLI-only, **no new
MCP tool** — the MCP server read path stays 100% local and read-only.

## User Stories

1. As a developer, I want `seam sync` to re-index only the files that changed since the last
   index, so that refreshing a large repo is fast instead of a full `seam init`.
2. As a developer, I want `seam sync` to detect files I added since the last index and index them,
   so that new code becomes queryable without a full re-index.
3. As a developer, I want `seam sync` to detect files I deleted and remove them from the index, so
   that `seam query`/`seam_context` never return symbols from files that no longer exist.
4. As a developer, I want unchanged files to be skipped without re-parsing, so that sync stays
   cheap and its cost scales with the size of my change, not the size of my repo.
5. As a developer, I want change detection to use the filesystem (mtime + content hash), not git,
   so that sync works in non-git repos and also catches committed changes from
   pull/checkout/merge/rebase.
6. As a developer, I want a file whose mtime changed but whose content is identical (e.g. `touch`,
   a checkout that rewrites the file byte-for-byte) to NOT be re-indexed, so that sync does no
   wasted work for no-op timestamp changes.
7. As a developer, I want `seam sync` to recompute clusters after reconcile so that `seam_clusters`
   reflects my edits, fixing the documented "clusters go stale after edits" gotcha.
8. As a developer, I want the cluster recompute to be skipped when nothing changed, so that running
   `seam sync` repeatedly with no edits is nearly free and doesn't churn cluster IDs.
9. As a developer, I want a `--force-clusters` flag that recomputes clusters even when zero files
   changed, so that I can refresh clusters after the live watcher already indexed my edits (leaving
   the file rows current but the clusters stale) without paying for a full re-index.
10. As a developer, I want `seam sync -q` to suppress all human output, so that I can wire it into a
    git hook as `( seam sync -q >/dev/null 2>&1 & )` without spamming the terminal.
11. As an AI agent, I want `seam sync --json` to return a structured envelope with the counts of
    added / modified / removed / unchanged / skipped files and whether clusters were recomputed, so
    that I can confirm the reconcile did what I expected and branch on the `ok` key.
12. As a developer, I want `seam sync` on a directory with no existing index to fail with a clear
    `NO_INDEX` error telling me to run `seam init` first, so that sync's responsibility (reconcile
    an existing index) stays distinct from init's (bootstrap one).
13. As a developer, I want `seam sync` to accept an optional path argument and `--db-dir` override
    exactly like `seam init`/`seam status`, so that the command surface is consistent and testable.
14. As a developer, I want one malformed/unreadable file to be skipped (counted as `skipped`) and
    not abort the whole sync, so that one bad file never strands my entire index — the same
    contract as `seam init`.
15. As a developer, I want `seam sync` to exit 0 on a successful reconcile (even one that changed
    nothing) and non-zero only on a real error (not a directory, DB failure), so that hooks and CI
    can rely on the exit code.
16. As a developer, I want the cluster recompute to reuse the exact same `index_clusters` pass that
    `seam init` uses (same naming mode, min-size, determinism), so that a synced index is
    indistinguishable from a freshly-`init`-ed one.
17. As a developer, I want `seam sync` to report an honest count of skipped files (binary / oversize
    / parse error), so that a systematic extraction failure is visible rather than silent.
18. As an AI agent, I want `seam sync` to never make a network call or require an API key (unless
    `SEAM_CLUSTER_NAMING=llm` is explicitly configured, exactly as `seam init` already behaves), so
    that it honors Seam's zero-external-services-at-runtime guarantee.
19. As a developer, I want running `seam sync` while the live watcher is also running to be safe
    (no corruption), relying on SQLite's `busy_timeout`, so that I am not forced to stop the daemon
    first.

## Implementation Decisions

- **New deep module `seam/indexer/sync.py`.** Single public function
  `sync(conn, root, *, recompute_clusters=True, force_clusters=False, naming_mode, llm_api_key,
  llm_model, min_size) -> SyncResult`. It owns no SQL schema and composes existing primitives only:
  `walk_project`, `index_one_file`, `delete_file`, `sha1` (all from the indexer layer) and
  `index_clusters`. It lives in `indexer/` (not `cli/`) for the same layering reason `pipeline.py`
  does — so the import hierarchy stays `cli → indexer.sync` (never the reverse).
- **`SyncResult` is a `TypedDict`** (consistent with `ContextPack`, `ContextResult`, etc.). Shape:
  ```
  SyncResult:
    added:               int   # files present on disk, absent from index → indexed
    modified:            int   # tracked files whose content hash changed → re-indexed
    removed:             int   # tracked files no longer on disk → deleted
    unchanged:           int   # tracked files skipped (mtime match, or hash match after touch)
    skipped:             int   # files index_one_file declined (unsupported/oversize/binary/error)
    graph_changed:       bool  # (added + modified + removed) > 0
    clusters_recomputed: bool  # whether index_clusters actually ran this sync
    cluster_count:       int | None  # result of index_clusters when it ran, else None
  ```
- **Change detection = mtime pre-filter → SHA-1 confirm.** Reuses the *existing* `files.mtime`
  (REAL, `st_mtime` at index time) and `files.file_hash` (SHA-1) columns — **no schema change, no
  `size` column.** A scanned file whose `st_mtime` equals the stored `mtime` is treated as
  unchanged without reading it. Otherwise the content is read + hashed; re-index happens only if
  the hash differs from the stored one. **Accepted blind spot** (identical to CodeGraph's): a
  content change that preserves mtime exactly is missed by the cheap path — `seam init` is the
  escape hatch (full re-index). This is documented, not silently dropped.
- **Cluster recompute is gated, and full (not incremental).** Because clusters are a *global*
  Louvain partition, there is no correct cheap incremental update — one new edge can re-partition
  unrelated communities. So sync runs the **same whole-graph `index_clusters`** that `seam init`
  runs, gated on `graph_changed OR force_clusters`. When the gate is false, `index_clusters` is not
  called at all (`clusters_recomputed=False`, `cluster_count=None`), so a no-op sync neither churns
  cluster IDs nor pays the Louvain cost.
- **`--force-clusters` flag** decouples "recompute clusters" from "files changed", covering the
  live-watcher-already-indexed case. It is the documented, honest completion of the gotcha fix.
- **Cluster pass reuses init's config verbatim** — `naming_mode`, `llm_api_key`, `llm_model`,
  `min_size` come from `seam/config.py` (`SEAM_CLUSTER_NAMING`, `SEAM_LLM_API_KEY`,
  `SEAM_LLM_MODEL`, `SEAM_CLUSTER_MIN_SIZE`), exactly as `init` passes them. A synced index is
  byte-equivalent to a freshly-init-ed one. **No new config knobs are introduced.**
- **Never raises.** Per-file failures inside `index_one_file` already return `None` (skipped); sync
  counts them and continues. A whole-sync failure (e.g. DB error) propagates to the CLI layer,
  which renders the structured error envelope — sync the function degrades gracefully, the CLI maps
  hard errors to exit codes.
- **CLI command `seam sync [path]`** in `seam/cli/main.py`, mirroring `init`'s path/`--db-dir`
  resolution. Flags: `--force-clusters`, `--quiet`/`-q`, `--json`. Uses the existing
  `check_mutual_exclusion` + `emit_json` / `emit_json_error` envelope path from `seam/cli/output.py`.
  On a directory with no `.seam/seam.db`, emits `NO_INDEX` (matching `seam status`) and exits 1.
- **No MCP tool.** `seam sync` is a maintenance/write command and joins `init`/`start`/`status` as
  CLI-only. The MCP server stays read-only — no `seam_sync` tool, MCP tool count stays 10.
- **No schema change, no migration.** Works on any index ≥ v4; the reconcile reads only existing
  columns and the cluster pass uses the existing `clusters` table + `cluster_id`.

## Testing Decisions

A good test here asserts the **observable reconcile outcome** — given an indexed fixture and a set
of filesystem mutations, `sync` returns the right counts, the DB reflects exactly those mutations,
and clusters are recomputed iff the gate says so. Do not assert SQL strings, hash values, or the
internal call sequence.

Modules under test (the three emphasized areas):

1. **`sync.py` reconcile + gating** (`tests/unit/test_sync.py`):
   - added file → indexed (`added==1`, its symbols present); deleted file → removed
     (`removed==1`, its symbols gone); content-changed file → re-indexed (`modified==1`, new
     symbols reflected); untouched file → `unchanged`, not re-parsed.
   - **mtime pre-filter**: a file whose stored mtime matches is classified `unchanged` without a
     content read. A `touch` (mtime bumped, content identical) is classified `unchanged` via the
     hash-confirm path — `modified==0`.
   - **gating**: a sync with zero changes does NOT recompute clusters (`clusters_recomputed==False`,
     `cluster_count is None`); a sync with ≥1 change DOES (`clusters_recomputed==True`);
     `force_clusters=True` recomputes even with zero changes.
   - one unreadable/oversize file → counted in `skipped`, sync completes, other files still
     processed.
   - Prior art: existing `seam init` indexing tests, `tests/unit/test_pack.py` (TypedDict result
     shape), `tests/` clustering tests.
2. **CLI command + envelope** (`tests/integration/test_sync_cli.py`):
   - `seam sync` after editing a fixture file updates the DB and prints the summary; `--json`
     returns a valid `{ok:true,data:{added,modified,removed,unchanged,skipped,graph_changed,
     clusters_recomputed,cluster_count}}` envelope; `--quiet` prints bare values; exit 0 on success.
   - `seam sync` on a directory with no index → `NO_INDEX` envelope (`--json`) / red message, exit 1.
   - `--json` + `--quiet` together → `INVALID_INPUT` (reuses `check_mutual_exclusion`).
   - Prior art: `tests/integration/test_pack_parity.py`, the `seam status --json/--quiet` tests, the
     `seam affected` CLI tests.
3. **End-to-end cluster freshness** (`tests/integration/test_sync_clusters.py`):
   - `seam init` → add a file that introduces a new connected symbol → `seam sync` → the new symbol
     now has a non-NULL `cluster_id` (gotcha fixed); cluster rows reflect the new graph.
   - a second `seam sync` with no changes leaves the `clusters` table untouched (IDs stable, no
     recompute).
   - `seam sync --force-clusters` with no file changes still recomputes clusters.
   - Prior art: the Phase 2 clustering integration tests and the homonym fixtures.

Run `make gate` (ruff + mypy + pytest) before every commit — must stay green.

## Out of Scope

- **Git-based change detection.** Detection is filesystem reconcile (mtime + hash) only, exactly
  like CodeGraph. No `git diff` / `git ls-files` integration.
- **A `size` column / any schema change / migration.** mtime + the existing SHA-1 hash are
  sufficient; adding `size` would only narrow the already-accepted mtime-preserving blind spot and
  is not worth a migration for MVP.
- **Incremental / scoped cluster recompute.** Seam's clusters are global; the recompute is full and
  gated, never partial. A scoped Louvain update is explicitly not attempted.
- **An MCP `seam_sync` tool.** sync is a maintenance/write command; the MCP server stays read-only.
- **Automatic git-hook installation.** sync only *provides* `-q` so a hook can call it. Writing the
  hook / agent config is the separate `seam install` work (roadmap item 8), out of scope here.
- **Changes to the live watcher daemon.** The watcher is untouched; `seam sync` is the one-shot
  complement to it, not a replacement.
- **Parallelism / worker-thread parsing.** Reconcile re-indexes sequentially, exactly like
  `seam init`. Concurrency is out of scope.
- **A file-level lock against a concurrent watcher.** Safety relies on SQLite's existing
  `busy_timeout`; sync does not add its own lock or a CodeGraph-style "zero-shape on lock
  contention" path.

## Further Notes

- **Known minor inefficiency (documented, accepted):** a file that is repeatedly `touch`-ed but
  never content-changed is re-hashed on every sync (its stored mtime is not advanced when content
  is unchanged, because we do not re-upsert an unchanged file). This costs one hash per touched
  file per sync — never a re-index — and matches CodeGraph's behavior. Advancing the stored mtime
  on a no-op would trade a read for a write; not worth it for MVP.
- The gate condition `graph_changed = (added + modified + removed) > 0` is a deliberately
  conservative proxy: any file change *may* alter the edge graph, so we recompute; zero file
  changes means the graph is identical, so we skip. A finer "did the edge set actually change"
  gate is more complex and not worth it for MVP.
- `seam sync` is the first half of roadmap §6.1; the git-hook *installer* and broader `install`
  command are roadmap item 8 (a later phase) and are not touched here.
