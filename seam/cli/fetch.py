"""seam fetch — download a pre-built Seam index artifact and land it locally.

Thin orchestration layer. All mechanics live in the S1/S2 leaves:
  - S1 (rebase_index):  seam/indexer/rebase.py — prefix-rewrite files.path
  - S2 (unpack_index):  seam/indexer/artifact.py — verify + extract .tar.gz
The local-delta reconcile reuses the existing sync path (sync_project).

End-to-end steps (per WS4 S3 spec):
  1. Validate SEAM_INDEX_ARTIFACT_URL is configured.
  2. Resolve the target git SHA: try HEAD first, walk first-parent ancestors
     (up to SEAM_FETCH_ANCESTOR_DEPTH) until a published artifact is found.
  3. Download the archive + checksum sidecar (urllib.request — supports both
     https:// and file://, so offline tests use file:// with zero network).
  4. Verify + unpack into a TEMP staging dir (S2 unpack_index).
  5. Atomic swap-in: existing .seam/ is renamed to .seam.fetch.bak/, the staged
     index is moved into .seam/, and the backup is deleted. On ANY failure the
     backup is restored — an existing index is NEVER corrupted.
  6. Rebase: rewrite files.path from the CI root to the local root (S1).
  7. Sync: reconcile the developer's local uncommitted delta (same args as
     `seam sync`).

Import contract (CLI layer — may import from all seam.* layers):
  stdlib only: logging, os, shutil, subprocess, tempfile, urllib.error,
               urllib.parse, urllib.request, from pathlib import Path
"""

import logging
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import seam.config as config
from seam.indexer.artifact import ARCHIVE_FILENAME, CHECKSUM_FILENAME, unpack_index
from seam.indexer.db import connect
from seam.indexer.embedding_index import sync_embeddings
from seam.indexer.rebase import rebase_index
from seam.indexer.sync import sync as sync_project

logger = logging.getLogger(__name__)


# ── Public exception ──────────────────────────────────────────────────────────


class FetchError(Exception):
    """Raised for expected, user-actionable failures during seam fetch.

    Attributes:
        code:    Stable UPPER_SNAKE error code (same vocabulary as CLI output.py).
        message: Human-readable explanation with a recommended action.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── Git helpers ───────────────────────────────────────────────────────────────


def _git_head_sha(project_root: Path) -> str:
    """Return the current HEAD SHA.

    Raises FetchError(NOT_A_GIT_REPO) when:
      - git is not installed / not on PATH
      - the directory is not inside a git repository
      - the git command times out
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise FetchError(
            "NOT_A_GIT_REPO",
            "git is not installed or not on PATH. "
            "Run 'seam init' to build the index locally.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError(
            "NOT_A_GIT_REPO",
            "git rev-parse timed out. "
            "Run 'seam init' to build the index locally.",
        ) from exc

    if result.returncode != 0:
        raise FetchError(
            "NOT_A_GIT_REPO",
            f"'{project_root}' is not a git repository (git rev-parse failed). "
            "Run 'seam init' to build the index locally.",
        )

    return result.stdout.strip()


def _git_first_parent_shas(project_root: Path, *, depth: int) -> list[str]:
    """Return first-parent ancestor SHAs, most-recent first, up to `depth`.

    Returns an empty list (never raises) on any git failure — the caller
    handles the empty-list case by using HEAD as the sole candidate.

    WHY first-parent: avoids walking into merged feature branches.
    WHY depth cap: prevents unbounded scans on long histories.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-list", "--first-parent", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    # rev-list emits newest first — exactly the order we want for fallback search.
    shas = result.stdout.strip().splitlines()
    return shas[:depth]


# ── URL helpers ───────────────────────────────────────────────────────────────


def _build_archive_url(sha: str, url_template: str) -> str:
    """Substitute {sha} into the URL template to get the archive URL."""
    return url_template.replace("{sha}", sha)


def _build_checksum_url(archive_url: str) -> str:
    """Derive the checksum sidecar URL from the archive URL.

    Replaces the archive filename (ARCHIVE_FILENAME) with the checksum filename
    (CHECKSUM_FILENAME) in the URL. This works for any URL scheme (https, file).
    """
    return archive_url.replace(ARCHIVE_FILENAME, CHECKSUM_FILENAME)


def _url_exists(url: str) -> bool:
    """Return True if the URL is accessible without fully downloading the resource.

    - For file:// URLs: checks path existence via the filesystem (zero I/O cost).
    - For http(s)://: sends a HEAD request to avoid downloading the full archive.

    Never raises — returns False on any error so the fallback loop can continue.

    WHY separate existence check (not just try-download): the ancestor-fallback
    loop needs a cheap way to probe multiple SHAs before committing to a download.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "file":
            # Convert file:// to a local path and stat it.
            local_path = Path(urllib.request.url2pathname(parsed.path))
            return local_path.is_file()
        # For http(s)://: send a lightweight HEAD request.
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return False


def _download_bytes(url: str) -> bytes:
    """Download `url` and return the raw bytes.

    Supports both https:// and file:// (for offline tests).
    Raises FetchError on any network or I/O failure.
    """
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(
            "FETCH_FAILED",
            f"HTTP {exc.code} downloading '{url}': {exc.reason}",
        ) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise FetchError(
            "FETCH_FAILED",
            f"Download failed for '{url}': {exc}",
        ) from exc


# ── SHA resolution with ancestor fallback ────────────────────────────────────


def _resolve_sha(
    project_root: Path,
    url_template: str,
    *,
    ancestor_depth: int,
) -> tuple[str, str]:
    """Resolve the (sha, archive_url) pair to download.

    Tries HEAD first, then walks first-parent ancestors (newest first) up to
    ancestor_depth. Returns the first SHA that has a published artifact.

    WHY HEAD first: the common case — CI just published for this exact commit.
    WHY ancestor fallback: a developer may have local commits on top of the
    last published SHA; we want to avoid forcing a full `seam init` in that case.

    Returns:
        (sha, archive_url) for the nearest ancestor with a published artifact.

    Raises:
        FetchError(FETCH_FAILED) when no artifact is found within ancestor_depth.
    """
    shas = _git_first_parent_shas(project_root, depth=ancestor_depth)
    if not shas:
        # Edge case: rev-list failed (shouldn't happen after _git_head_sha passed,
        # but be defensive). Fall back to just HEAD.
        shas = [_git_head_sha(project_root)]

    for sha in shas:
        url = _build_archive_url(sha, url_template)
        if _url_exists(url):
            logger.info("fetch: found artifact at sha=%s", sha[:8])
            return sha, url

    raise FetchError(
        "FETCH_FAILED",
        f"No published artifact found within {ancestor_depth} ancestors. "
        f"Tried HEAD and first-parent ancestors. "
        f"Run 'seam init' to build the index locally, or publish an artifact first.",
    )


# ── Core orchestration ────────────────────────────────────────────────────────


def fetch_index(
    project_root: Path,
    *,
    db_root: Path | None = None,
    semantic: bool = False,
) -> dict[str, Any]:
    """Download, verify, unpack, rebase, and sync a pre-built Seam index.

    Args:
        project_root: Local project root (must be a git repository).
        db_root:      Override for the directory that holds .seam/ (mirrors
                      --db-dir in init/sync). Defaults to project_root.
        semantic:     When True, also run incremental semantic embedding after
                      sync (mirrors `seam sync --semantic`).

    Returns:
        A result dict with:
          - sha:              Git SHA of the fetched artifact.
          - bytes_downloaded: Raw bytes of the archive.
          - files_rebased:    Number of files.path rows rewritten by rebase.
          - sync:             SyncResult dict from the reconcile step.

    Raises:
        FetchError: On any user-actionable failure (unset URL, not a git repo,
                    download error, checksum mismatch). The error code and message
                    are structured for the CLI to surface via emit_json_error.

    Safety contract (most important property):
        An existing .seam/ is NEVER corrupted on failure. The staging → swap
        sequence is: stage in tmp → rename existing → move staged → delete backup.
        On ANY exception after the rename, the backup is restored before re-raising.

    Checksum leniency:
        The sha256 sidecar (ARCHIVE_FILENAME.sha256) is downloaded alongside the
        archive.  When it is PRESENT, verification is enforced: a mismatch aborts
        the fetch with FetchError.  When the sidecar download returns a 404 (or any
        HTTP/I/O error), a WARNING is logged and the fetch proceeds WITHOUT checksum
        verification — so a CI setup that publishes archives but not sidecars still
        works.  This leniency is intentional and documented here so it is not
        mistaken for an oversight.  If you need mandatory verification, ensure your
        CI always publishes the sidecar alongside the archive.
    """
    # ── Step 1: Validate URL template ─────────────────────────────────────────
    url_template = config.SEAM_INDEX_ARTIFACT_URL
    if not url_template:
        raise FetchError(
            "INVALID_INPUT",
            "SEAM_INDEX_ARTIFACT_URL is not set. "
            "Set it to the artifact URL template (containing {sha}) "
            "or run 'seam init' to build the index locally.",
        )

    # ── Step 2: Validate git repo + resolve SHA + archive URL ─────────────────
    # _git_head_sha raises FetchError(NOT_A_GIT_REPO) if not a git repo or git absent.
    # We call it first so the NOT_A_GIT_REPO error surfaces before any URL probe.
    _git_head_sha(project_root)  # raises on non-git; result not used here

    sha, archive_url = _resolve_sha(
        project_root,
        url_template,
        ancestor_depth=config.SEAM_FETCH_ANCESTOR_DEPTH,
    )
    checksum_url = _build_checksum_url(archive_url)

    # ── Step 3: Download into a temp directory ─────────────────────────────────
    # WHY temp dir: we never download directly into .seam/ to preserve the
    # atomic-swap contract. If the download fails mid-way, the existing .seam/ is
    # completely untouched.
    with tempfile.TemporaryDirectory(prefix="seam-fetch-") as _tmp:
        tmp_dir = Path(_tmp)
        archive_local = tmp_dir / ARCHIVE_FILENAME
        checksum_local = tmp_dir / CHECKSUM_FILENAME

        # Download the archive
        archive_bytes = _download_bytes(archive_url)
        archive_local.write_bytes(archive_bytes)
        bytes_downloaded = len(archive_bytes)
        logger.info("fetch: downloaded %d bytes from %s", bytes_downloaded, archive_url)

        # Download the checksum sidecar (optional — skip verification if absent)
        has_checksum = False
        try:
            checksum_bytes = _download_bytes(checksum_url)
            checksum_local.write_bytes(checksum_bytes)
            has_checksum = True
        except FetchError:
            logger.warning(
                "fetch: checksum sidecar not available (%s); skipping verification",
                checksum_url,
            )

        # ── Step 4: Verify + unpack into a staging dir ──────────────────────
        stage_dir = tmp_dir / "staged"
        ok = unpack_index(
            archive_local,
            dest_dir=stage_dir,
            checksum_path=checksum_local if has_checksum else None,
        )
        if not ok:
            raise FetchError(
                "FETCH_FAILED",
                "Archive verification or extraction failed. "
                "The artifact may be corrupt or the checksum mismatched. "
                "Run 'seam init' to build the index locally.",
            )

        # ── Step 5: Atomic swap-in ──────────────────────────────────────────
        # Layout: .seam/ → .seam.fetch.bak/ (backup), staged/ → .seam/ (new)
        # On ANY exception after backup: restore backup → re-raise.
        db_base = db_root if db_root is not None else project_root
        seam_dir = db_base / ".seam"
        seam_bak = db_base / ".seam.fetch.bak"

        # Remove stale backup from a prior interrupted fetch (makes swap idempotent)
        if seam_bak.exists():
            shutil.rmtree(seam_bak, ignore_errors=True)

        had_existing = seam_dir.exists()
        try:
            if had_existing:
                # Move existing .seam/ aside; fast on same filesystem (rename).
                seam_dir.rename(seam_bak)

            # Move staged dir into .seam/ position.
            # shutil.copytree is used instead of rename because stage_dir is inside
            # the TemporaryDirectory (potentially a different mount point on some
            # systems). copytree is always safe across filesystems.
            shutil.copytree(str(stage_dir), str(seam_dir))

        except Exception as exc:
            # Restore backup so the original index is not lost.
            if had_existing and seam_bak.exists():
                if seam_dir.exists():
                    shutil.rmtree(seam_dir, ignore_errors=True)
                try:
                    seam_bak.rename(seam_dir)
                except OSError as restore_exc:
                    # Last-resort log: the restore itself failed. This is extremely
                    # unlikely (disk full during restore) but we surface it clearly.
                    logger.error(
                        "fetch: CRITICAL — failed to restore backup .seam/: %s "
                        "(backup at '%s' may still be usable)",
                        restore_exc,
                        seam_bak,
                    )
            raise FetchError(
                "FETCH_FAILED",
                f"Failed to swap in the staged index: {exc}. "
                "The original index (if any) has been preserved.",
            ) from exc

        # Swap succeeded — safe to remove backup.
        if seam_bak.exists():
            shutil.rmtree(seam_bak, ignore_errors=True)
    # TemporaryDirectory cleaned up here (archive + staging gone)

    # ── Step 6: Rebase — rewrite files.path from CI root to local root ────────
    db_path = config.get_db_path(db_base)
    try:
        conn = connect(db_path)
    except Exception as exc:
        raise FetchError("DB_ERROR", f"Failed to open downloaded index: {exc}") from exc

    try:
        files_rebased = rebase_index(conn, new_root=str(project_root))
    finally:
        conn.close()

    logger.info("fetch: rebased %d file path(s) to '%s'", files_rebased, project_root)

    # ── Step 7: Sync — reconcile developer's local uncommitted delta ──────────
    # WHY: the artifact was built at a prior git state. The developer may have
    # files on disk that differ from that state (local edits, new files after the
    # ancestor-fallback SHA). sync_project re-indexes those deltas so queries
    # reflect the actual on-disk state.
    try:
        sync_conn = connect(db_path)
    except Exception as exc:
        raise FetchError("DB_ERROR", f"Failed to open index for sync: {exc}") from exc

    try:
        sync_result = sync_project(
            sync_conn,
            project_root,
            recompute_clusters=True,
            force_clusters=False,
            naming_mode=config.SEAM_CLUSTER_NAMING,
            llm_api_key=config.SEAM_LLM_API_KEY,
            llm_model=config.SEAM_LLM_MODEL,
            min_size=config.SEAM_CLUSTER_MIN_SIZE,
            synthesis_enabled=config.SEAM_EDGE_SYNTHESIS == "on",
            force_synthesis=False,
            fanout_cap=config.SEAM_SYNTHESIS_FANOUT_CAP,
        )
    finally:
        sync_conn.close()

    # ── Optional: semantic embedding sync ─────────────────────────────────────
    if semantic:
        # WHY: same pattern as `seam sync --semantic`; only embed missing symbols.
        try:
            embed_conn = connect(db_path)
            try:
                sync_embeddings(embed_conn, model=config.SEAM_EMBED_MODEL, batch=32)
            finally:
                embed_conn.close()
        except Exception as exc:  # noqa: BLE001
            # Non-fatal: semantic embedding failure does not invalidate the index.
            logger.warning("fetch: embeddings sync failed (non-fatal): %s", exc)

    return {
        "sha": sha,
        "bytes_downloaded": bytes_downloaded,
        "files_rebased": files_rebased,
        "sync": dict(sync_result),
    }
