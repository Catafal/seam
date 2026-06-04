"""Whole-index confidence resolver — single source of truth for the
EXTRACTED / AMBIGUOUS / INFERRED rule.

Phase 5 additions:
  - Resolution TypedDict: confidence + resolved_by + best_candidate.
  - resolve_edge(): full resolver with provenance, builtin filtering, import
    promotion (step A), and proximity tie-break (step D).
  - load_import_mappings(): load a file's import bindings from the DB.
  - resolve() kept as a backward-compat thin shim.

Resolution rule (scope: whole index, evaluated at read time):
  1. Same-file import binds target to exactly one indexed file → EXTRACTED, 'import'.
  2. name count == 1 → EXTRACTED, 'name-unique'.
     name count >  1 → AMBIGUOUS, 'name-collision' (+ proximity best_candidate).
  3. count == 0 AND is_builtin(name, lang) → INFERRED, 'builtin'.
     count == 0 AND not builtin → INFERRED, 'unresolved'.

Design rationale:
  Confidence is a property of *global* state (the full index), not a per-file
  property.  Resolving at read time means it is always fresh after any
  incremental watcher re-index — no write-amplification, no staleness.
  The stored edges.confidence column is a same-file lower-bound hint kept for
  debugging; read-time resolution here is authoritative and overrides it.

Import rules (no circular deps):
  This module imports ONLY stdlib + seam.analysis.builtins + seam.analysis.imports.
  It must NOT import traversal.py, flows.py, or any other seam analysis module.
  traversal.py and flows.py import their confidence constants FROM here.
  builtins.py and imports.py are LEAVES — they import only stdlib.
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import TypedDict

import seam.config as config
from seam.analysis.builtins import is_builtin
from seam.analysis.imports import (
    ImportMapping,
    compute_path_proximity,
    resolve_import_source,
)

logger = logging.getLogger(__name__)

# ── Module-level "already warned" guard ──────────────────────────────────────
# Prevents the empty-import_mappings warning from firing on every hop of a hot traversal.
# Set to True on the first emission; never reset so it fires at most once per process.
_import_mappings_empty_warned: bool = False

# ── Canonical confidence constants ────────────────────────────────────────────
# These are the three possible values for the confidence field on edges and hops.
# All other seam modules (traversal, flows) import them from here.

CONFIDENCE_EXTRACTED = "EXTRACTED"  # target name is unique in the full index
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"  # target name matches >1 indexed symbol
CONFIDENCE_INFERRED = "INFERRED"  # target not indexed (external, stdlib, dynamic)

# ── resolved_by vocabulary (Phase 5) ──────────────────────────────────────────
# Stable string enum — surfaced verbatim in MCP/CLI output.
# null/None for pre-v6 / unresolved-context rows (same null-contract as Phase 4 fields).
RESOLVED_BY_IMPORT = "import"  # promoted via a resolved same-file import
RESOLVED_BY_NAME_UNIQUE = "name-unique"  # name appears exactly once in the index
RESOLVED_BY_COLLISION = "name-collision"  # name appears >1 times (homonym)
RESOLVED_BY_BUILTIN = "builtin"  # name is a known language builtin (count==0)
RESOLVED_BY_UNRESOLVED = "unresolved"  # count==0, not a builtin


class Resolution(TypedDict):
    """Result of resolve_edge(): confidence tier + provenance + optional tie-break.

    Fields:
        confidence:    EXTRACTED | AMBIGUOUS | INFERRED
        resolved_by:   how the tier was decided (see RESOLVED_BY_* constants above)
                       None for pre-v6 / unknown-context rows (null-contract)
        best_candidate: for AMBIGUOUS edges, the most file-path-proximate declaring file
                        path (None when not applicable or no proximity data available)
    """

    confidence: str
    resolved_by: str | None
    best_candidate: str | None


# ── DB helper ─────────────────────────────────────────────────────────────────


def load_name_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Load a name → count map from the symbols table in a single GROUP BY query.

    This is the only DB call this module makes.  Called once per query in
    traversal.walk / flows.trace / flows.callers / flows.callees so that
    confidence is resolved against the full index without per-edge round-trips.

    Args:
        conn: Open SQLite connection (read-only semantics; no writes).

    Returns:
        dict mapping every symbol name to its occurrence count across all files.
        An empty dict when the symbols table has no rows.
    """
    rows = conn.execute("SELECT name, COUNT(*) AS cnt FROM symbols GROUP BY name").fetchall()
    # Positional access works under any row_factory (and db.connect() sets sqlite3.Row).
    result: dict[str, int] = {row[0]: row[1] for row in rows}
    if not result:
        # Empty map → EVERY edge resolves to INFERRED (the exact silent degradation
        # issue #9 fixed). Surface it loudly: an empty symbols table almost always
        # means the index was never built or is mid-rebuild, not that the code is
        # genuinely all-external. Without this, the regression returns invisibly.
        logger.warning(
            "load_name_counts: symbols table is empty — all edge confidence will "
            "resolve to INFERRED. Run 'seam init' to (re)build the index."
        )
    else:
        logger.debug("load_name_counts: %d distinct symbol names loaded", len(result))
    return result


def load_import_mappings(
    conn: sqlite3.Connection,
    file_path: str,
) -> list[ImportMapping]:
    """Load import mappings for a file from the import_mappings table.

    Single DB query, mirrors load_name_counts() design pattern.
    Returns [] if the table doesn't exist (pre-v6 DB) or the file is unknown.
    Never raises.

    Emits a once-per-process warning when the import_mappings table is empty while
    symbols exist — almost always a stale pre-v6 index that needs 'seam init'.
    Mirroring load_name_counts()'s empty-index warning pattern.

    Args:
        conn:       Open SQLite connection (read-only semantics).
        file_path:  Absolute path of the referencing file (as stored in files.path).
    """
    global _import_mappings_empty_warned  # noqa: PLW0603 — module-level once-warning guard
    try:
        rows = conn.execute(
            """
            SELECT im.local_name, im.exported_name, im.source_module,
                   im.is_default, im.is_namespace, im.is_wildcard, im.line
            FROM import_mappings im
            JOIN files f ON f.id = im.file_id
            WHERE f.path = ?
            """,
            (file_path,),
        ).fetchall()

        # Guard: only warn when the TABLE is empty, not just this file (which may import nothing).
        # The extra two SELECTs are cheap because they run at most once per process.
        if not rows and not _import_mappings_empty_warned:
            try:
                any_mapping = conn.execute("SELECT 1 FROM import_mappings LIMIT 1").fetchone()
                any_symbol = conn.execute("SELECT 1 FROM symbols LIMIT 1").fetchone()
                if any_mapping is None and any_symbol is not None:
                    _import_mappings_empty_warned = True
                    logger.warning(
                        "load_import_mappings: import_mappings table is empty but symbols exist — "
                        "run 'seam init' to enable import promotion (Phase 5 resolution)."
                    )
            except Exception:  # noqa: BLE001
                pass  # pre-v6 DB — table absent, warning is moot

        return [
            ImportMapping(
                local_name=row["local_name"],
                exported_name=row["exported_name"],
                source_module=row["source_module"],
                is_default=bool(row["is_default"]),
                is_namespace=bool(row["is_namespace"]),
                is_wildcard=bool(row["is_wildcard"]),
                line=row["line"],
            )
            for row in rows
        ]
    except Exception:  # noqa: BLE001
        # Table absent (pre-v6) or any other error → degrade gracefully
        return []


# P4 — barrel directory-entry filenames. A directory import (`from './barrel'`)
# resolves to one of these inside that directory. TS/JS only — the languages
# where the barrel/index.* convention exists. Ordered by precedence.
_BARREL_INDEX_FILES = ("index.ts", "index.tsx", "index.js", "index.mjs", "index.cjs")


def _resolve_barrel_source(
    source_module: str,
    referencing_file: Path,
    repo_root: Path,
    language: str,
    max_import_candidates: int,
) -> list[str]:
    """Resolve an import source, adding TS/JS directory→index.* barrel probing.

    `resolve_import_source` does not map a directory import (`./barrel`) to its
    `index.ts` entry (the TS extension order has no `/index.*`). Barrels ARE
    directory imports, so this P4-local helper falls back to probing for a
    barrel index file inside the resolved directory when the normal resolution
    returns nothing. Only applies to TS/JS; never raises.
    """
    hits = resolve_import_source(source_module, referencing_file, repo_root, language)[
        :max_import_candidates
    ]
    if hits or language not in ("typescript", "javascript"):
        return hits

    # Directory-import barrel fallback: probe <dir>/index.* for relative sources.
    if not (source_module.startswith("./") or source_module.startswith("../")):
        return hits
    try:
        base = Path(os.path.normpath(referencing_file.parent / source_module))
    except Exception:  # noqa: BLE001
        return hits
    for index_name in _BARREL_INDEX_FILES:
        candidate = base / index_name
        if candidate.exists():
            return [str(candidate)]
    return hits


def _chase_barrel(
    exported: str,
    barrel_paths: list[str],
    repo_root: Path,
    conn: sqlite3.Connection,
    language: str,
    max_import_candidates: int,
    depth: int,
    visited: set[tuple[str, str]],
) -> str | None:
    """Follow a re-export chain through barrel files to the real declarer.

    P4 — barrel re-export following. A barrel (e.g. index.ts) re-exports a name
    from a sibling but does not declare it. This walks each barrel's OWN
    import_mappings for `exported`, resolves the re-export source to file(s),
    checks whether those declare the name, and recurses if they are themselves
    barrels — up to `depth` more hops.

    Returns the single declaring file path on success, or None when the chain
    dead-ends, branches ambiguously, exceeds depth, or cycles. Never raises.

    Args:
        exported:       The exported name being chased (stable across hops here —
                        re-exports modeled as non-aliased named bindings).
        barrel_paths:   Candidate barrel file paths to inspect at this hop.
        repo_root:      Repo root for import-source resolution.
        conn:           DB connection (read-only).
        language:       Language of the chain (TS/JS barrels in practice).
        max_import_candidates: Per-import candidate cap (perf bound).
        depth:          Remaining hops allowed (decremented per recursion).
        visited:        (file, name) pairs already seen — cycle + repeat-DB guard.
    """
    if depth <= 0:
        return None

    for barrel_path in barrel_paths:
        key = (barrel_path, exported)
        if key in visited:
            # Cycle or already-explored barrel — skip to guarantee termination
            # and avoid repeated DB hits (the per-resolution cache requirement).
            continue
        visited.add(key)

        # Load THIS barrel's own re-export mappings for the exported name.
        barrel_maps = load_import_mappings(conn, barrel_path)
        try:
            barrel_dir = Path(barrel_path)
        except Exception:  # noqa: BLE001
            continue

        for m in barrel_maps:
            if m["is_wildcard"] or m["is_namespace"]:
                continue
            # Match on the local binding the barrel re-exports under (the name a
            # consumer sees) — equals `exported` for plain `export { X } from`.
            if m["local_name"] != exported:
                continue

            next_exported = m["exported_name"]
            next_paths = _resolve_barrel_source(
                m["source_module"], barrel_dir, repo_root, language,
                max_import_candidates,
            )
            if not next_paths:
                continue

            # Does any resolved file actually DECLARE the name? → real declarer.
            declaring = _declaring_files(conn, next_exported, next_paths)
            if len(declaring) == 1:
                return declaring[0]
            if len(declaring) > 1:
                # Branches to multiple declarers → genuinely ambiguous; stop.
                return None

            # None declare it → those files are themselves barrels; recurse.
            hit = _chase_barrel(
                next_exported, next_paths, repo_root, conn, language,
                max_import_candidates, depth - 1, visited,
            )
            if hit is not None:
                return hit

    return None


def _declaring_files(
    conn: sqlite3.Connection,
    name: str,
    candidate_paths: list[str],
) -> list[str]:
    """Return which of candidate_paths declare `name` in the index. Never raises."""
    try:
        rows = conn.execute(
            """
            SELECT f.path FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE s.name = ? AND f.path IN ({})
            """.format(",".join("?" * len(candidate_paths))),
            [name, *candidate_paths],
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [row["path"] for row in rows]


def _resolve_with_import_promotion(
    target_name: str,
    name_counts: dict[str, int],
    import_mappings: list[ImportMapping],
    referencing_file: Path,
    repo_root: Path,
    conn: sqlite3.Connection,
    language: str,
    max_import_candidates: int,
    max_proximity_candidates: int,
) -> Resolution:
    """Attempt import-promotion resolution (step A).

    Checks same-file import mappings for a binding of target_name to exactly
    one indexed declaring file. If found, promotes to EXTRACTED 'import'.

    P4: when the resolved source file does NOT itself declare the exported name
    (i.e. it is a barrel that re-exports from siblings), the chain is followed
    through the barrel's OWN import_mappings up to SEAM_BARREL_DEPTH hops to find
    the real declarer (bounded + cycle-safe + cached per (file, name)).

    Falls through to name-count resolution when:
    - No import binding found.
    - Import source resolves to no indexed file (third-party).
    - Resolved file does NOT declare the exported name AND no barrel chain
      reaches a single declarer (prevents false promotion).
    - Import is wildcard (no specific binding — star import, story 27).

    Args are pre-validated by the caller; this function does not guard them.
    """
    # Scan import mappings for a binding of target_name.
    # Wildcards and namespace imports are skipped: a wildcard has no specific exported-name
    # binding to look up, and a namespace import binds the whole module object rather
    # than an individual symbol, so neither can narrow the target to a declaring file.
    for mapping in import_mappings:
        if mapping["is_wildcard"]:
            continue
        if mapping["is_namespace"]:
            continue
        if mapping["local_name"] != target_name:
            continue

        # This import binds target_name locally. Resolve the source module to file paths.
        # When barrel following is enabled, a directory import (`from './barrel'`)
        # additionally resolves to its index.* entry so the chain can be chased;
        # SEAM_BARREL_DEPTH=0 uses the plain resolver → byte-identical to pre-P4.
        if config.SEAM_BARREL_DEPTH > 0:
            candidate_paths = _resolve_barrel_source(
                mapping["source_module"],
                referencing_file,
                repo_root,
                language,
                max_import_candidates,
            )
        else:
            candidate_paths = resolve_import_source(
                mapping["source_module"],
                referencing_file,
                repo_root,
                language,
            )[:max_import_candidates]

        if not candidate_paths:
            # Third-party or unresolvable source — fall through to name-count rule.
            # Debug log so "why AMBIGUOUS not EXTRACTED" is diagnosable without reading source.
            logger.debug(
                "_resolve_with_import_promotion: %r — source %r unresolvable (third-party/out-of-scope)",
                target_name,
                mapping["source_module"],
            )
            continue

        # Check which candidate files actually declare the exported name in the index.
        exported = mapping["exported_name"]
        declaring_paths = _declaring_files(conn, exported, candidate_paths)
        if len(declaring_paths) == 1:
            # Exactly one indexed file declares the name → promote to EXTRACTED
            return Resolution(
                confidence=CONFIDENCE_EXTRACTED,
                resolved_by=RESOLVED_BY_IMPORT,
                best_candidate=declaring_paths[0],
            )
        # 0 or >1 declaring files — can't promote directly; fall through.
        # Debug logs so "why AMBIGUOUS not EXTRACTED" is answerable without reading source.
        if not declaring_paths:
            # P4: the resolved source declares nothing — it may be a BARREL that
            # re-exports the name from a sibling. Chase the re-export chain
            # (bounded by SEAM_BARREL_DEPTH, cycle-safe, cached per (file, name))
            # to find the real declarer. SEAM_BARREL_DEPTH=0 disables → pre-P4.
            if config.SEAM_BARREL_DEPTH > 0:
                hit = _chase_barrel(
                    exported=exported,
                    barrel_paths=candidate_paths,
                    repo_root=repo_root,
                    conn=conn,
                    language=language,
                    max_import_candidates=max_import_candidates,
                    depth=config.SEAM_BARREL_DEPTH,
                    visited=set(),
                )
                if hit is not None:
                    return Resolution(
                        confidence=CONFIDENCE_EXTRACTED,
                        resolved_by=RESOLVED_BY_IMPORT,
                        best_candidate=hit,
                    )
            logger.debug(
                "_resolve_with_import_promotion: %r — exported name %r not declared in resolved file(s) %s",
                target_name,
                exported,
                candidate_paths,
            )
        else:
            logger.debug(
                "_resolve_with_import_promotion: %r — %d declaring files found (>1, ambiguous): %s",
                target_name,
                len(declaring_paths),
                declaring_paths,
            )

    # No import binding matched — fall through to name-count rule.
    logger.debug(
        "_resolve_with_import_promotion: %r — no import binding matched, using name-count rule",
        target_name,
    )
    return _resolve_name_count(
        target_name,
        name_counts,
        language=language,
        referencing_file=referencing_file,
        max_proximity_candidates=max_proximity_candidates,
        conn=conn,
    )


def _resolve_name_count(
    target_name: str,
    name_counts: dict[str, int],
    language: str | None = None,
    referencing_file: Path | None = None,
    max_proximity_candidates: int = 25,
    conn: sqlite3.Connection | None = None,
    candidate_files: list[str] | None = None,
) -> Resolution:
    """Name-count resolution rule with builtin filtering and proximity tie-break.

    Resolution order:
      count == 1 → EXTRACTED, 'name-unique'.
      count >  1 → AMBIGUOUS, 'name-collision' + proximity best_candidate if possible.
      count == 0 AND is_builtin(name, lang) → INFERRED, 'builtin'.
      count == 0 AND not builtin → INFERRED, 'unresolved'.

    The builtin check fires ONLY when count==0 — structural guarantee for story 5.
    """
    count = name_counts.get(target_name, 0)

    if count == 1:
        return Resolution(
            confidence=CONFIDENCE_EXTRACTED,
            resolved_by=RESOLVED_BY_NAME_UNIQUE,
            best_candidate=None,
        )

    if count > 1:
        # AMBIGUOUS — proximity narrows the best_candidate hint but keeps the tier
        # AMBIGUOUS because path distance alone can't guarantee the correct target.
        best = _proximity_best_candidate(
            target_name=target_name,
            referencing_file=referencing_file,
            max_candidates=max_proximity_candidates,
            conn=conn,
            candidate_files=candidate_files,
        )
        return Resolution(
            confidence=CONFIDENCE_AMBIGUOUS,
            resolved_by=RESOLVED_BY_COLLISION,
            best_candidate=best,
        )

    # count == 0: check builtins ONLY here (structural guard for story 5).
    # A user-defined name with count >= 1 can NEVER reach this branch.
    # SEAM_BUILTIN_FILTERING="off" disables builtin tagging entirely.
    if language and config.SEAM_BUILTIN_FILTERING == "on" and is_builtin(target_name, language):
        return Resolution(
            confidence=CONFIDENCE_INFERRED,
            resolved_by=RESOLVED_BY_BUILTIN,
            best_candidate=None,
        )

    return Resolution(
        confidence=CONFIDENCE_INFERRED,
        resolved_by=RESOLVED_BY_UNRESOLVED,
        best_candidate=None,
    )


def _proximity_best_candidate(
    target_name: str,
    referencing_file: Path | None,
    max_candidates: int,
    conn: sqlite3.Connection | None,
    candidate_files: list[str] | None = None,
) -> str | None:
    """Return the file path of the most proximately-close declaring symbol.

    Used for step D: AMBIGUOUS edge tie-break by file-path proximity.
    Returns None when insufficient context is available.

    Args:
        target_name:      Symbol name to look up declaring files for.
        referencing_file: The file that references the symbol (for proximity calc).
        max_candidates:   Maximum declaring files to evaluate (performance cap).
        conn:             DB connection for querying declaring files (may be None).
        candidate_files:  Pre-provided list of declaring file paths (for testing).
    """
    if referencing_file is None:
        return None

    # Get candidate declaring file paths (from conn or direct)
    paths: list[str] = []
    if candidate_files is not None:
        paths = candidate_files[:max_candidates]
    elif conn is not None:
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT f.path FROM symbols s
                JOIN files f ON f.id = s.file_id
                WHERE s.name = ?
                LIMIT ?
                """,
                (target_name, max_candidates),
            ).fetchall()
            paths = [row["path"] for row in rows]
        except Exception:  # noqa: BLE001
            return None

    if not paths:
        return None

    # Rank by path proximity — higher score = same directory or closer tree
    best_path: str | None = None
    best_score: int = -1
    for p in paths:
        try:
            score = compute_path_proximity(referencing_file, Path(p))
        except Exception:  # noqa: BLE001
            score = 0
        if score > best_score:
            best_score = score
            best_path = p

    return best_path


# ── Public resolvers ───────────────────────────────────────────────────────────


def resolve_edge(
    target_name: str,
    name_counts: dict[str, int],
    language: str | None = None,
    import_mappings: list[ImportMapping] | None = None,
    referencing_file: Path | None = None,
    repo_root: Path | None = None,
    conn: sqlite3.Connection | None = None,
    max_import_candidates: int = 25,
    max_proximity_candidates: int = 25,
    candidate_files: list[str] | None = None,
) -> Resolution:
    """Full Phase 5 resolver: returns a Resolution with confidence + resolved_by.

    Resolution order (four-step):
      1. If import_mappings provided AND a non-wildcard mapping binds target_name
         to exactly one indexed declaring file → EXTRACTED, 'import' (story A).
      2. name-count rule:
         count == 1 → EXTRACTED, 'name-unique'.
         count >  1 → AMBIGUOUS, 'name-collision' + proximity best_candidate (D).
      3. count == 0 AND is_builtin(name, language) → INFERRED, 'builtin' (C).
      4. count == 0 AND not builtin → INFERRED, 'unresolved'.

    The builtin check fires ONLY at count==0 (structural guarantee for story 5).
    A user-defined name with count >= 1 is NEVER filtered as builtin.

    Degrades gracefully: any missing context (no language, no mappings, etc.)
    falls back to the name-count rule. Never raises.

    Args:
        target_name:           The edge target name to resolve.
        name_counts:           Whole-index name → count map (from load_name_counts).
        language:              Language of the referencing file. Required for builtin
                               check and import source resolution. None → no builtin check.
        import_mappings:       Parsed import bindings for the referencing file
                               (from load_import_mappings). None → skip step A.
        referencing_file:      Path to the referencing source file. Required for
                               import source resolution and proximity scoring.
        repo_root:             Repository root. Required for import source resolution.
        conn:                  DB connection. Required for step A declaration check
                               and step D proximity query. None → limited resolution.
        max_import_candidates: Cap on candidate declaring files per import (perf).
        max_proximity_candidates: Cap on candidates for proximity ranking (perf).
        candidate_files:       Pre-provided candidate file paths for testing proximity
                               without a real DB connection.
    """
    try:
        # Step A: import promotion — beats global collision (the homonym fix).
        # Requires SEAM_IMPORT_RESOLUTION="on" (default); skip when disabled.
        if (
            config.SEAM_IMPORT_RESOLUTION == "on"
            and import_mappings is not None
            and referencing_file is not None
            and repo_root is not None
            and conn is not None
            and language is not None
        ):
            return _resolve_with_import_promotion(
                target_name=target_name,
                name_counts=name_counts,
                import_mappings=import_mappings,
                referencing_file=referencing_file,
                repo_root=repo_root,
                conn=conn,
                language=language,
                max_import_candidates=max_import_candidates,
                max_proximity_candidates=max_proximity_candidates,
            )

        # Steps 2-4: name-count rule (+ builtin check at count==0)
        return _resolve_name_count(
            target_name=target_name,
            name_counts=name_counts,
            language=language,
            referencing_file=referencing_file,
            max_proximity_candidates=max_proximity_candidates,
            conn=conn,
            candidate_files=candidate_files,
        )
    except Exception:  # noqa: BLE001
        # Degrade gracefully: any failure falls back to plain name-count string
        return Resolution(
            confidence=resolve(target_name, name_counts),
            resolved_by=None,
            best_candidate=None,
        )


# ── Pure resolver ──────────────────────────────────────────────────────────────


def resolve(target_name: str, name_counts: dict[str, int]) -> str:
    """Resolve confidence for a single edge target against the whole-index name map.

    Pure function — no I/O, no side effects.

    Args:
        target_name:  The edge target (callee / importee) name to resolve.
        name_counts:  Mapping produced by load_name_counts(conn).

    Returns:
        CONFIDENCE_EXTRACTED  if the name appears exactly once in the index.
        CONFIDENCE_AMBIGUOUS  if the name appears more than once.
        CONFIDENCE_INFERRED   if the name is absent (count == 0 or missing key).
    """
    count = name_counts.get(target_name, 0)
    if count == 1:
        return CONFIDENCE_EXTRACTED
    if count > 1:
        return CONFIDENCE_AMBIGUOUS
    # count == 0: name not in index — external library, stdlib, or dynamic call.
    return CONFIDENCE_INFERRED
