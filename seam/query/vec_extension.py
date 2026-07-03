"""sqlite-vec extension capability probe and loader — WS2b S1.

A LEAF module: imports only stdlib (logging, sqlite3) plus the lazily-imported
optional package sqlite_vec. No seam.config, no server, no CLI.

WHY this exists:
  WS2b adds an optional ANN (approximate-nearest-neighbour) acceleration tier
  backed by the sqlite-vec extension.  Before any ANN operation (index build or
  KNN query) the process must check whether sqlite-vec can actually be loaded:
    - some Python builds (notably macOS system SQLite) disable
      conn.enable_load_extension() entirely;
    - the sqlite-vec package may not be installed ([semantic-ann] is optional);
    - the load call itself may fail on unusual platforms.
  This module is the single source of truth for "can this Python process do ANN
  via sqlite-vec?".  S2 (ANN index builder) and S3 (three-tier read path) both
  call it; neither re-implements the guarded probe sequence.

Public API:
  probe_vec_extension(conn)  → bool   (full create/drop round-trip probe)
  load_vec_extension(conn)   → bool   (load extension for reuse, no probe table)

Design decisions (see .claude/runs/ws2b-s1-vec-probe/implementation-notes.html):
  - sqlite_vec is imported LAZILY inside the function body so this module is
    importable even when [semantic-ann] is not installed.
  - probe_vec_extension opens its own :memory: connection for the CREATE/DROP
    test so it has NO side-effects on the caller's database (no WAL writes, no
    locking).  The capability signal is identical.
  - enable_load_extension(False) is ALWAYS restored in a finally block so the
    caller's connection is left in the default restricted state.
  - Never raises: every failure path catches the exception, logs exactly ONE
    WARNING that names the reason, and returns False.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)

# ── Public constants ──────────────────────────────────────────────────────────
# Shape used for the throwaway probe table.  4-dimensional float vectors are the
# smallest valid vec0 spec; the exact dimensionality is irrelevant for a probe.
_PROBE_TABLE = "seam_vec_probe_tmp"
_PROBE_DIM = 4


# ── probe_vec_extension ───────────────────────────────────────────────────────


def probe_vec_extension(conn: sqlite3.Connection) -> bool:
    """Full capability probe: can this process load sqlite-vec and create a vec0 table?

    Performs the complete guarded sequence:
      1. Enable extension loading on a fresh :memory: connection (NOT on `conn`
         — the probe must be side-effect-free on the caller's database).
      2. Lazily import sqlite_vec.
      3. Load the sqlite-vec extension into the probe connection.
      4. CREATE a throwaway vec0 virtual table, then immediately DROP it.
      5. Disable extension loading on the probe connection (defensive cleanup).

    Returns True only when all five steps succeed.
    Returns False and logs exactly ONE WARNING on ANY failure, naming the cause.

    The caller's `conn` is not mutated.  Pass it only so future overloads can
    inspect the connection path for platform-specific behaviour if needed.

    WHY probe separately from load_vec_extension:
      The probe is the gate ("can we do ANN at all?").  load_vec_extension is the
      "now actually load it for use" step called only after the probe passes.
      Separating them makes each step independently testable.
    """
    probe_conn: sqlite3.Connection | None = None
    try:
        # Step 1 — enable extension loading on a disposable in-memory connection.
        # We intentionally open a fresh connection so the probe CREATE/DROP leaves
        # no trace on the caller's database (no WAL entries, no locking).
        probe_conn = sqlite3.connect(":memory:")
        probe_conn.enable_load_extension(True)

        # Step 2 & 3 — lazy import + load.  ImportError → package absent.
        try:
            import sqlite_vec  # noqa: PLC0415 (lazy import is intentional)
        except ImportError as exc:
            logger.warning(
                "sqlite-vec extension unavailable (package not installed: %s) "
                "— semantic search will use the brute-force fallback",
                exc,
            )
            return False

        try:
            sqlite_vec.load(probe_conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sqlite-vec extension unavailable (load failed: %s: %s) "
                "— semantic search will use the brute-force fallback",
                type(exc).__name__,
                exc,
            )
            return False

        # Step 4 — create/drop probe: confirms vec0 virtual tables actually work.
        try:
            probe_conn.execute(
                f"CREATE VIRTUAL TABLE {_PROBE_TABLE} "
                f"USING vec0(embedding float[{_PROBE_DIM}])"
            )
            probe_conn.execute(f"DROP TABLE {_PROBE_TABLE}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sqlite-vec extension unavailable (vec0 probe failed: %s: %s) "
                "— semantic search will use the brute-force fallback",
                type(exc).__name__,
                exc,
            )
            return False

        logger.debug("sqlite-vec extension probe succeeded (v%s)", _vec_version(probe_conn))
        return True

    except (AttributeError, sqlite3.OperationalError, Exception) as exc:  # noqa: BLE001
        # Covers: enable_load_extension not available (AttributeError on some builds),
        # OperationalError ("not authorized") on builds that disable extension loading,
        # and any other unexpected error during the setup steps.
        logger.warning(
            "sqlite-vec extension unavailable (enable_load_extension failed: %s: %s) "
            "— semantic search will use the brute-force fallback",
            type(exc).__name__,
            exc,
        )
        return False

    finally:
        # Always clean up the probe connection.  Disabling extension loading is
        # best-effort here (the connection is about to be closed).
        if probe_conn is not None:
            try:
                probe_conn.enable_load_extension(False)
            except Exception:  # noqa: BLE001
                pass
            try:
                probe_conn.close()
            except Exception:  # noqa: BLE001
                pass


# ── load_vec_extension ────────────────────────────────────────────────────────


def load_vec_extension(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension onto `conn` for reuse by KNN queries.

    Unlike probe_vec_extension, this function leaves the extension LOADED on
    `conn` so subsequent SQL statements (MATCH, vec_version(), etc.) work for
    the connection's lifetime.

    Returns True on success, False and exactly ONE WARNING on any failure.
    Never raises.

    Call sequence (mirrors the probe but without the CREATE/DROP dance):
      1. conn.enable_load_extension(True)
      2. import sqlite_vec  (lazy)
      3. sqlite_vec.load(conn)
      4. conn.enable_load_extension(False)  ← blocks further extension loads
         (the already-loaded sqlite-vec extension stays active)

    WHY disable after loading:
      Leaving extension loading enabled is a security risk (any subsequent SQL
      can load arbitrary shared libraries).  The standard pattern is to enable,
      load the trusted extension, then disable immediately.  The loaded extension
      remains active for the connection's lifetime.
    """
    try:
        conn.enable_load_extension(True)
    except (AttributeError, sqlite3.OperationalError, Exception) as exc:  # noqa: BLE001
        logger.warning(
            "sqlite-vec extension unavailable (enable_load_extension failed: %s: %s) "
            "— semantic search will use the brute-force fallback",
            type(exc).__name__,
            exc,
        )
        return False

    try:
        try:
            import sqlite_vec  # noqa: PLC0415 (lazy import is intentional)
        except ImportError as exc:
            logger.warning(
                "sqlite-vec extension unavailable (package not installed: %s) "
                "— semantic search will use the brute-force fallback",
                exc,
            )
            return False

        try:
            sqlite_vec.load(conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sqlite-vec extension unavailable (load failed: %s: %s) "
                "— semantic search will use the brute-force fallback",
                type(exc).__name__,
                exc,
            )
            return False

        logger.debug("sqlite-vec extension loaded successfully")
        return True

    finally:
        # Disable extension loading in all cases (success or failure) so the
        # connection cannot load further arbitrary shared libraries.
        try:
            conn.enable_load_extension(False)
        except Exception:  # noqa: BLE001
            pass


# ── Private helpers ───────────────────────────────────────────────────────────


def _vec_version(conn: sqlite3.Connection) -> str:
    """Return vec_version() string from the connection, or '?' on any error."""
    try:
        row = conn.execute("SELECT vec_version()").fetchone()
        return str(row[0]) if row else "?"
    except Exception:  # noqa: BLE001
        return "?"
