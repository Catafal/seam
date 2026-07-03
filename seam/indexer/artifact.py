"""Index artifact pack/unpack/verify leaf — canonical .tar.gz format for portable index sharing.

This module is the SINGLE source of truth for the Seam index archive format so
the CI-side producer and consumers (`seam fetch`, `seam inspect-index`,
`seam import-index`) can never drift.

Archive format:
  - A .tar.gz file containing a flat (no subdirectories) set of files:
      * seam.db          — always present (the SQLite index)
      * vectors.f32      — optional (WS2a vector store; included when present)
      * vectors.ids.i64  — optional (WS2a id sidecar; included when present)
      * vectors.meta.json — optional (WS2a metadata; included when present)
      * manifest.json    — always present in newly exported artifacts
    Excluded: out/, watcher.pid, .gitignore, diagnostics.ndjson, any -wal/-shm sidecars.
  - A sha256 checksum sidecar (same name with .sha256 extension).

WHY flat archive (no subdirectory nesting): the consumer unpacks into a fresh .seam/
directory. A flat layout maps naturally — no path-stripping needed.

WHY single source of truth: pack and unpack live in the same module. The list of
canonical members (CANONICAL_FILES) is declared once. Producer and consumer share it;
format drift cannot happen.

Import contract (leaf discipline):
  - ONLY stdlib: dataclasses, hashlib, importlib.metadata, io, json, logging,
    os, pathlib, sqlite3, subprocess, tarfile, datetime, typing.
  - NEVER imports from seam.cli, seam.server, or any module that imports those layers.
  - Config knob (SEAM_INDEX_ARTIFACT_URL) is defined in seam/config.py; this leaf
    does NOT read it — callers that need the URL read it from config directly.

Security contract (path-traversal guard):
  - unpack_index validates EVERY member before extracting ANY member.
  - Any member with an absolute path or a '..' path component causes an immediate abort.
  - This is ALL-OR-NOTHING: either all members pass and all are extracted, or nothing
    is written to disk. A partially-extracted malicious archive is worse than no archive.

Never-raises contract:
  - All public functions catch every exception, log a warning, and return None/False.
  - This is intentional leaf discipline: callers (CLI, future fetch) never need try/except
    around these functions.
"""

import dataclasses
import hashlib
import importlib.metadata
import io
import json
import logging
import os
import sqlite3
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

# ── Archive filename constants ────────────────────────────────────────────────

# Canonical archive name. Fixed so CI scripts can refer to a predictable artifact path.
# The sha256 inside the sidecar is the content fingerprint — no need to embed it in
# the filename.
ARCHIVE_FILENAME = "seam-index.tar.gz"
CHECKSUM_FILENAME = "seam-index.sha256"
MANIFEST_FILENAME = "manifest.json"
MANIFEST_VERSION = 1

# ── Canonical file list ───────────────────────────────────────────────────────

# Mandatory: must be present in .seam/ for pack to succeed.
_REQUIRED_FILES = frozenset({"seam.db"})

# Optional: included in the archive when they exist in .seam/ (WS2a vector store).
# WHY these three: vectors.f32 (float32 matrix), vectors.ids.i64 (id sidecar),
# vectors.meta.json (model/dim/count metadata). Together they form the mmap store.
# Including partial sets (e.g. only vectors.meta.json) would be confusing — but we
# include whatever subset is present so partial stores can be shared for debugging.
_OPTIONAL_FILES = frozenset(
    {
        "vectors.f32",
        "vectors.ids.i64",
        "vectors.meta.json",
    }
)

# Complete set of files eligible for inclusion in the archive.
CANONICAL_FILES = _REQUIRED_FILES | _OPTIONAL_FILES | frozenset({MANIFEST_FILENAME})

# ── Public dataclass ──────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class PackResult:
    """Result of a successful pack_index call.

    Attributes:
        archive_path:  Absolute path to the produced .tar.gz file.
        checksum_path: Absolute path to the sha256 sidecar.
        size_bytes:    Byte size of the archive file.
        checksum:      sha256 hex digest of the archive (64 chars, lowercase).
        manifest:      Portable artifact metadata stored inside the archive.
    """

    archive_path: Path
    checksum_path: Path
    size_bytes: int
    checksum: str  # sha256 hex, 64 chars
    manifest: dict[str, Any]


# ── Public API ────────────────────────────────────────────────────────────────


def pack_index(
    seam_dir: Path,
    *,
    dest_dir: Path | None = None,
    project_root: Path | None = None,
) -> PackResult | None:
    """Pack a .seam/ index directory into a canonical .tar.gz + sha256 sidecar.

    Args:
        seam_dir:  Path to the .seam/ directory to pack.
        dest_dir:  Directory where the archive and sidecar are written.
                   Defaults to seam_dir.parent (i.e. the project root).
        project_root: Repository root used for git metadata and the artifact
                      repository fingerprint. Defaults to seam_dir.parent.

    Returns:
        PackResult on success; None on any failure (never raises).

    Failure conditions (logged at WARNING, return None):
        - seam_dir does not exist or is not a directory.
        - seam.db is absent (the archive would be meaningless without it).
        - Any I/O error during archive creation.
    """
    try:
        return _pack_index_impl(seam_dir, dest_dir=dest_dir, project_root=project_root)
    except Exception as exc:  # noqa: BLE001
        # Never-raises contract: catch-all so callers never see an unhandled exception.
        logger.warning("artifact.pack_index: unexpected error (returning None): %s", exc)
        return None


def verify_archive(archive_path: Path, checksum_path: Path) -> bool:
    """Verify a .tar.gz archive against its sha256 sidecar.

    Reads the digest from the first whitespace-delimited token of the sidecar
    (compatible with shasum -a 256 / sha256sum output format).

    Args:
        archive_path:  Path to the .tar.gz archive.
        checksum_path: Path to the .sha256 sidecar.

    Returns:
        True if the computed sha256 of archive_path matches the sidecar digest.
        False on any mismatch or I/O error (never raises).
    """
    try:
        return _verify_impl(archive_path, checksum_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("artifact.verify_archive: unexpected error (returning False): %s", exc)
        return False


def unpack_index(
    archive_path: Path,
    *,
    dest_dir: Path,
    checksum_path: Path | None = None,
) -> bool:
    """Verify and unpack a .tar.gz archive into dest_dir with path-traversal guards.

    Security contract (ALL-OR-NOTHING):
        All members are validated for safe paths before ANY file is extracted.
        A single bad member aborts the entire operation — no partial extraction.

    Args:
        archive_path:  Path to the .tar.gz archive.
        dest_dir:      Directory into which files are extracted.
        checksum_path: Optional path to the sha256 sidecar.
                       When provided, the checksum is verified before extraction.
                       When None, extraction proceeds without verification.

    Returns:
        True on success; False on any failure (never raises).

    Failure conditions (logged at WARNING, return False):
        - Archive file does not exist or is corrupt.
        - Checksum mismatch (when checksum_path is provided).
        - Any member with an absolute path or a '..' component.
        - Any I/O error during extraction.
    """
    try:
        return _unpack_impl(archive_path, dest_dir=dest_dir, checksum_path=checksum_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("artifact.unpack_index: unexpected error (returning False): %s", exc)
        return False


def inspect_artifact(
    archive_path: Path,
    *,
    checksum_path: Path | None = None,
) -> dict[str, Any] | None:
    """Read artifact metadata without extracting or mutating the live index."""
    try:
        return _inspect_artifact_impl(archive_path, checksum_path=checksum_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("artifact.inspect_artifact: unexpected error (returning None): %s", exc)
        return None


def artifact_has_manifest(archive_path: Path) -> bool | None:
    """Return whether an archive contains a manifest, or None if it cannot be read."""
    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            return any(member.name == MANIFEST_FILENAME for member in tf.getmembers())
    except (tarfile.TarError, OSError) as exc:
        logger.warning(
            "artifact.artifact_has_manifest: failed to read '%s': %s",
            archive_path,
            exc,
        )
        return None


# ── Internal implementations ──────────────────────────────────────────────────


def _pack_index_impl(
    seam_dir: Path,
    *,
    dest_dir: Path | None,
    project_root: Path | None,
) -> PackResult | None:
    """Inner (raising) implementation — wrapped by pack_index for safety."""
    # ── Validate inputs ───────────────────────────────────────────────────────
    if not seam_dir.is_dir():
        logger.warning("artifact.pack_index: '%s' is not a directory; cannot pack", seam_dir)
        return None

    db_path = seam_dir / "seam.db"
    if not db_path.is_file():
        logger.warning(
            "artifact.pack_index: seam.db not found in '%s'; cannot pack a meaningless archive",
            seam_dir,
        )
        return None

    # ── Resolve destination ───────────────────────────────────────────────────
    # Default: place archive in the project root (parent of .seam/).
    # WHY: keeps the archive separate from the index it was built from, avoiding
    # the awkward situation where the archive is INSIDE the directory it describes.
    out_dir = dest_dir if dest_dir is not None else seam_dir.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    archive_path = out_dir / ARCHIVE_FILENAME
    checksum_path = out_dir / CHECKSUM_FILENAME

    # ── Collect canonical files ───────────────────────────────────────────────
    # Always include required files; include optional files only when present.
    files_to_pack: list[tuple[str, Path | bytes]] = []

    artifact_files = sorted(_REQUIRED_FILES | _OPTIONAL_FILES)
    for filename in artifact_files:
        candidate = seam_dir / filename
        if filename in _REQUIRED_FILES:
            # Already verified seam.db above; add unconditionally.
            files_to_pack.append((filename, candidate))
        else:
            # Optional: include only when the file actually exists.
            if candidate.is_file():
                files_to_pack.append((filename, candidate))

    manifest = _build_manifest(
        seam_dir,
        files=[name for name, _ in files_to_pack],
        project_root=project_root,
    )
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    files_to_pack.append((MANIFEST_FILENAME, manifest_bytes))

    # ── Write archive to a temp file then rename (atomic write) ───────────────
    # WHY temp + rename: if we crash mid-write, we don't leave a truncated archive
    # at the canonical path. Only a complete, valid archive ever appears there.
    tmp_archive = archive_path.with_suffix(".tar.gz.tmp")
    try:
        _write_tar_gz(tmp_archive, files_to_pack)
        os.replace(tmp_archive, archive_path)  # atomic on POSIX; near-atomic on Windows
    finally:
        # Clean up the temp file on any failure (ignore errors in the finally block).
        if tmp_archive.exists():
            try:
                tmp_archive.unlink()
            except OSError:
                pass

    # ── Compute sha256 + write sidecar ────────────────────────────────────────
    digest = _sha256_file(archive_path)
    # Format mirrors `sha256sum` / `shasum -a 256` output: "<hex>  <filename>"
    # This makes the sidecar compatible with standard shell verification:
    #   sha256sum --check seam-index.sha256
    checksum_path.write_text(f"{digest}  {ARCHIVE_FILENAME}\n", encoding="utf-8")

    size_bytes = archive_path.stat().st_size

    logger.info(
        "artifact.pack_index: wrote archive '%s' (%d bytes, sha256=%s…)",
        archive_path,
        size_bytes,
        digest[:12],
    )

    return PackResult(
        archive_path=archive_path,
        checksum_path=checksum_path,
        size_bytes=size_bytes,
        checksum=digest,
        manifest=manifest,
    )


def _write_tar_gz(dest: Path, files: list[tuple[str, Path | bytes]]) -> None:
    """Write a .tar.gz with a flat layout (member names only, no directories).

    WHY flat layout: the consumer unpacks into .seam/ directly. A flat archive
    means the consumer can do a simple extract-all and gets the right layout.
    Subdirectory nesting would require path-stripping, complicating unpack.
    """
    with tarfile.open(dest, "w:gz", compresslevel=9) as tf:
        for member_name, source in files:
            # Explicit TarInfo keeps member names flat even when source files live
            # under different local checkout paths.
            if isinstance(source, bytes):
                info = tarfile.TarInfo(name=member_name)
                info.size = len(source)
                tf.addfile(info, io.BytesIO(source))
                continue
            info = tf.gettarinfo(str(source), arcname=member_name)
            with open(source, "rb") as fh:
                tf.addfile(info, fh)


def _build_manifest(
    seam_dir: Path,
    *,
    files: list[str],
    project_root: Path | None,
) -> dict[str, Any]:
    """Build the portable trust summary before archive bytes are written."""
    db_path = seam_dir / "seam.db"
    schema_version = _read_schema_version(db_path)
    root = project_root.resolve() if project_root is not None else seam_dir.parent.resolve()
    git = read_repository_identity(root)
    return {
        "manifest_version": MANIFEST_VERSION,
        "artifact_format": "seam-index",
        "created_at": datetime.now(UTC).isoformat(),
        "producer": {
            "name": "seam-code",
            "version": _seam_distribution_version(),
        },
        "schema_version": schema_version,
        "repository": {
            "root_fingerprint": compute_root_fingerprint(root),
            "git_head": git.get("head"),
            "git_remote": git.get("remote"),
        },
        "contents": {
            "files": sorted(files),
            "has_embeddings": any(name.startswith("vectors.") for name in files),
            "has_source_text": False,
            "has_diagnostics": False,
        },
    }


def _read_schema_version(db_path: Path) -> int | None:
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _seam_distribution_version() -> str:
    try:
        return importlib.metadata.version("seam-code")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def compute_root_fingerprint(root: Path) -> str:
    """Return the local checkout identity used for artifact compatibility checks."""
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def read_repository_identity(root: Path) -> dict[str, str | None]:
    """Return git evidence used to compare artifact and checkout compatibility."""
    return {
        "head": _git_output(root, "rev-parse", "HEAD"),
        "remote": _git_output(root, "config", "--get", "remote.origin.url"),
    }


def _git_output(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def _sha256_file(path: Path) -> str:
    """Compute the sha256 hex digest of a file by reading it in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_impl(archive_path: Path, checksum_path: Path) -> bool:
    """Inner (raising) verify implementation."""
    if not archive_path.is_file():
        logger.warning("artifact.verify_archive: archive not found: '%s'", archive_path)
        return False

    if not checksum_path.is_file():
        logger.warning("artifact.verify_archive: checksum sidecar not found: '%s'", checksum_path)
        return False

    # Parse the sidecar: extract the first whitespace-delimited token (the hex digest).
    sidecar_text = checksum_path.read_text(encoding="utf-8").strip()
    expected_hex = sidecar_text.split()[0].lower()

    actual_hex = _sha256_file(archive_path)

    if actual_hex != expected_hex:
        logger.warning(
            "artifact.verify_archive: checksum MISMATCH for '%s' (expected=%s…, actual=%s…)",
            archive_path,
            expected_hex[:12],
            actual_hex[:12],
        )
        return False

    return True


def _inspect_artifact_impl(
    archive_path: Path,
    *,
    checksum_path: Path | None,
) -> dict[str, Any] | None:
    if checksum_path is not None and not _verify_impl(archive_path, checksum_path):
        return None
    if not archive_path.is_file():
        logger.warning("artifact.inspect_artifact: archive not found: '%s'", archive_path)
        return None
    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            members = tf.getmembers()
            for member in members:
                if not _is_safe_member(member.name) or member.issym() or member.islnk():
                    logger.warning(
                        "artifact.inspect_artifact: rejected unsafe member '%s'",
                        member.name,
                    )
                    return None
                if member.name not in CANONICAL_FILES:
                    logger.warning(
                        "artifact.inspect_artifact: rejected unexpected member '%s'",
                        member.name,
                    )
                    return None
            manifest_member = tf.extractfile(MANIFEST_FILENAME)
            if manifest_member is None:
                logger.warning(
                    "artifact.inspect_artifact: manifest '%s' not found in '%s'",
                    MANIFEST_FILENAME,
                    archive_path,
                )
                return None
            manifest = json.loads(manifest_member.read().decode("utf-8"))
    except (tarfile.TarError, json.JSONDecodeError, OSError) as exc:
        logger.warning("artifact.inspect_artifact: failed to inspect '%s': %s", archive_path, exc)
        return None
    return {
        "archive": str(archive_path),
        "checksum": _sha256_file(archive_path),
        "checksum_file": str(checksum_path) if checksum_path is not None else None,
        "size_bytes": archive_path.stat().st_size,
        "members": sorted(member.name for member in members),
        "manifest": manifest,
    }


def _is_safe_member(name: str) -> bool:
    """Return True only when a tar member name is safe to extract into a destination dir.

    WHY this check is necessary: a malicious or corrupt archive can contain members
    with absolute paths (/etc/passwd) or parent-traversal components (../../evil).
    tarfile does NOT guard against this by default in Python < 3.12's data filter.

    Conservative approach: reject BOTH absolute paths AND any component equal to '..'.
    This is stricter than just checking the final resolved path (which might be safe
    after normalization) — but strictness here is correct: we are unpacking ONLY our
    own canonical archives which never have absolute paths or '..' components.

    Note: symlink and hardlink member-type checks are done separately in _unpack_impl
    (on the TarInfo object) because that information is not part of the member name.
    """
    if os.path.isabs(name):
        return False

    # Check every path component for '..'. We use PurePosixPath because tar member
    # names always use forward slashes regardless of the OS.
    parts = PurePosixPath(name).parts
    if ".." in parts:
        return False

    return True


def _unpack_impl(
    archive_path: Path,
    *,
    dest_dir: Path,
    checksum_path: Path | None,
) -> bool:
    """Inner (raising) unpack implementation."""
    # ── Optional checksum verification (before touching dest_dir) ─────────────
    if checksum_path is not None:
        if not _verify_impl(archive_path, checksum_path):
            logger.warning("artifact.unpack_index: aborting unpack due to checksum mismatch")
            return False

    # ── Open the archive and validate ALL members before extracting ANY ────────
    # WHY validate-all first: if any member is bad we refuse the whole archive.
    # This prevents a partially-extracted state where safe files land on disk but
    # we then discover a malicious member — a rollback would be complex and error-prone.
    if not archive_path.is_file():
        logger.warning("artifact.unpack_index: archive not found: '%s'", archive_path)
        return False

    try:
        with tarfile.open(archive_path, "r:gz") as tf:
            members = tf.getmembers()

            # ── Validation pass ───────────────────────────────────────────────
            for member in members:
                if not _is_safe_member(member.name):
                    logger.warning(
                        "artifact.unpack_index: SECURITY — rejected unsafe member '%s' "
                        "in '%s'; aborting unpack",
                        member.name,
                        archive_path,
                    )
                    return False
                # Defense-in-depth: reject symlinks and hardlinks regardless of
                # their linkname.  Our canonical archives never contain links, so
                # a link member means the archive is corrupt or malicious.
                if member.issym() or member.islnk():
                    logger.warning(
                        "artifact.unpack_index: SECURITY — rejected symlink/hardlink "
                        "member '%s' in '%s'; aborting unpack",
                        member.name,
                        archive_path,
                    )
                    return False

            # ── Extraction pass (all members are safe) ────────────────────────
            dest_dir.mkdir(parents=True, exist_ok=True)
            for member in members:
                # filter='data' is explicit and matches the Python 3.14+ default.
                # It strips special attributes and refuses unsafe member types as a
                # second line of defence (our validation pass above is the first).
                tf.extract(member, path=dest_dir, set_attrs=False, filter="data")

    except tarfile.TarError as exc:
        logger.warning("artifact.unpack_index: failed to read archive '%s': %s", archive_path, exc)
        return False

    logger.info(
        "artifact.unpack_index: extracted %d files from '%s' into '%s'",
        len(members),
        archive_path,
        dest_dir,
    )
    return True
