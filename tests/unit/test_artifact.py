"""Tests for seam/indexer/artifact.py — pack/unpack/verify leaf.

All tests are OFFLINE (no network). The leaf is tested through its public API:
  - pack_index(seam_dir, *, dest_dir) -> PackResult | None
  - verify_archive(archive_path, checksum_path) -> bool
  - unpack_index(archive_path, *, dest_dir, checksum_path=None) -> bool

TDD order: each group of tests was written BEFORE the corresponding implementation.
"""

import io
import tarfile
from pathlib import Path

from seam.indexer.artifact import PackResult, pack_index, unpack_index, verify_archive

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_seam_dir(tmp_path: Path, *, with_vectors: bool = False) -> Path:
    """Create a minimal .seam/ directory with a fake seam.db."""
    seam_dir = tmp_path / ".seam"
    seam_dir.mkdir()
    (seam_dir / "seam.db").write_bytes(b"SQLite format 3\x00fake database content for testing")
    if with_vectors:
        (seam_dir / "vectors.f32").write_bytes(b"\x01\x02\x03\x04" * 4)
        (seam_dir / "vectors.ids.i64").write_bytes(b"\x00\x00\x00\x00\x00\x00\x00\x01")
        (seam_dir / "vectors.meta.json").write_text('{"model": "BAAI/bge-small-en-v1.5", "dim": 384}')
    return seam_dir


# ── Pack: archive + checksum produced ────────────────────────────────────────


def test_pack_returns_pack_result(tmp_path: Path) -> None:
    """pack_index returns a PackResult dataclass (not None) for a valid index."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert isinstance(result, PackResult)


def test_pack_archive_file_exists(tmp_path: Path) -> None:
    """The archive file referenced by PackResult.archive_path exists on disk."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    assert result.archive_path.exists()


def test_pack_archive_is_tar_gz(tmp_path: Path) -> None:
    """The archive has a .tar.gz extension and is a valid gzip-compressed tar."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    assert result.archive_path.name.endswith(".tar.gz")
    # Verify it is actually a readable tar.gz (not just a renamed file)
    assert tarfile.is_tarfile(str(result.archive_path))


def test_pack_checksum_file_exists(tmp_path: Path) -> None:
    """A sha256 checksum sidecar is written alongside the archive."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    assert result.checksum_path.exists()


def test_pack_checksum_is_valid_sha256_hex(tmp_path: Path) -> None:
    """The checksum file contains a 64-char hex string (sha256)."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    text = result.checksum_path.read_text().strip()
    # Format: "<64-hex>  <filename>" (shasum -a 256 style) OR bare hex
    hex_part = text.split()[0]
    assert len(hex_part) == 64
    int(hex_part, 16)  # raises ValueError if not valid hex


def test_pack_result_checksum_matches_file(tmp_path: Path) -> None:
    """PackResult.checksum matches what is in the checksum_path sidecar."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    text = result.checksum_path.read_text().strip()
    hex_part = text.split()[0]
    assert result.checksum == hex_part


def test_pack_result_size_bytes_correct(tmp_path: Path) -> None:
    """PackResult.size_bytes matches the actual file size of the archive."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    assert result.size_bytes == result.archive_path.stat().st_size
    assert result.size_bytes > 0


# ── Pack: canonical contents ──────────────────────────────────────────────────


def test_pack_contains_seam_db(tmp_path: Path) -> None:
    """seam.db is always included in the archive."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    with tarfile.open(result.archive_path, "r:gz") as tf:
        names = tf.getnames()
    assert "seam.db" in names


def test_pack_excludes_gitignore(tmp_path: Path) -> None:
    """.gitignore is NOT included in the archive."""
    seam_dir = _make_seam_dir(tmp_path)
    (seam_dir / ".gitignore").write_text("*\n")
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    with tarfile.open(result.archive_path, "r:gz") as tf:
        names = tf.getnames()
    assert ".gitignore" not in names


def test_pack_excludes_watcher_pid(tmp_path: Path) -> None:
    """watcher.pid is NOT included in the archive."""
    seam_dir = _make_seam_dir(tmp_path)
    (seam_dir / "watcher.pid").write_text("12345\n")
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    with tarfile.open(result.archive_path, "r:gz") as tf:
        names = tf.getnames()
    assert "watcher.pid" not in names


def test_pack_excludes_out_directory(tmp_path: Path) -> None:
    """out/ directory and its contents are NOT included in the archive."""
    seam_dir = _make_seam_dir(tmp_path)
    out_dir = seam_dir / "out"
    out_dir.mkdir()
    (out_dir / "impact-foo.json").write_text('{"ok":true}')
    result = pack_index(seam_dir, dest_dir=tmp_path / "archives")
    assert result is not None
    with tarfile.open(result.archive_path, "r:gz") as tf:
        names = tf.getnames()
    # Neither the directory nor any file inside it should appear
    assert not any("out" in n for n in names)


def test_pack_keyword_only_index_omits_vector_files(tmp_path: Path) -> None:
    """Keyword-only index (no vectors.f32 etc.) packs successfully; no vector entries."""
    seam_dir = _make_seam_dir(tmp_path, with_vectors=False)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    with tarfile.open(result.archive_path, "r:gz") as tf:
        names = tf.getnames()
    assert "seam.db" in names
    assert "vectors.f32" not in names
    assert "vectors.ids.i64" not in names
    assert "vectors.meta.json" not in names


def test_pack_includes_all_vector_files_when_present(tmp_path: Path) -> None:
    """All three WS2a vector-store files are included when they exist."""
    seam_dir = _make_seam_dir(tmp_path, with_vectors=True)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    with tarfile.open(result.archive_path, "r:gz") as tf:
        names = tf.getnames()
    assert "seam.db" in names
    assert "vectors.f32" in names
    assert "vectors.ids.i64" in names
    assert "vectors.meta.json" in names


def test_pack_includes_only_canonical_files(tmp_path: Path) -> None:
    """Extra files in .seam/ that are not in the canonical list are excluded."""
    seam_dir = _make_seam_dir(tmp_path, with_vectors=True)
    # Add a stray file that should be excluded
    (seam_dir / "diagnostics.ndjson").write_text("{}\n")
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    with tarfile.open(result.archive_path, "r:gz") as tf:
        names = tf.getnames()
    assert "diagnostics.ndjson" not in names


# ── Pack: graceful failure ─────────────────────────────────────────────────────


def test_pack_missing_db_returns_none(tmp_path: Path) -> None:
    """pack_index returns None (never raises) when seam.db is absent."""
    seam_dir = tmp_path / ".seam"
    seam_dir.mkdir()
    # No seam.db — graceful failure expected
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is None


def test_pack_missing_seam_dir_returns_none(tmp_path: Path) -> None:
    """pack_index returns None when the .seam/ directory itself does not exist."""
    seam_dir = tmp_path / "nonexistent" / ".seam"
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is None


def test_pack_never_raises(tmp_path: Path) -> None:
    """pack_index never raises, even on nonsensical input."""
    # Pass a file path where a directory is expected
    (tmp_path / "not_a_dir").write_bytes(b"x")
    result = pack_index(tmp_path / "not_a_dir", dest_dir=tmp_path / "out")
    assert result is None


# ── Verify ─────────────────────────────────────────────────────────────────────


def test_verify_returns_true_for_correct_checksum(tmp_path: Path) -> None:
    """verify_archive returns True when the sidecar checksum matches the archive."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    assert verify_archive(result.archive_path, result.checksum_path) is True


def test_verify_returns_false_for_tampered_checksum(tmp_path: Path) -> None:
    """verify_archive returns False when the sidecar checksum does not match."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    # Overwrite the sidecar with a wrong digest
    result.checksum_path.write_text("0" * 64 + "  seam-index.tar.gz\n")
    assert verify_archive(result.archive_path, result.checksum_path) is False


def test_verify_returns_false_for_tampered_archive(tmp_path: Path) -> None:
    """verify_archive returns False when the archive bytes have been altered."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    # Append garbage bytes to corrupt the archive (while keeping the original checksum)
    with open(result.archive_path, "ab") as fh:
        fh.write(b"\x00\xff\x00")
    assert verify_archive(result.archive_path, result.checksum_path) is False


def test_verify_missing_archive_returns_false(tmp_path: Path) -> None:
    """verify_archive returns False (never raises) when the archive file is missing."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    result.archive_path.unlink()
    assert verify_archive(result.archive_path, result.checksum_path) is False


def test_verify_missing_checksum_file_returns_false(tmp_path: Path) -> None:
    """verify_archive returns False when the checksum sidecar is missing."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    result.checksum_path.unlink()
    assert verify_archive(result.archive_path, result.checksum_path) is False


# ── Unpack: round-trip ─────────────────────────────────────────────────────────


def test_unpack_round_trip_seam_db(tmp_path: Path) -> None:
    """Round-trip pack→unpack reproduces byte-identical seam.db."""
    seam_dir = _make_seam_dir(tmp_path)
    original_db = (seam_dir / "seam.db").read_bytes()
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None

    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(result.archive_path, dest_dir=dest, checksum_path=result.checksum_path)
    assert ok is True
    assert (dest / "seam.db").read_bytes() == original_db


def test_unpack_round_trip_vector_files(tmp_path: Path) -> None:
    """Round-trip pack→unpack reproduces byte-identical vector store files."""
    seam_dir = _make_seam_dir(tmp_path, with_vectors=True)
    original_vectors = (seam_dir / "vectors.f32").read_bytes()
    original_ids = (seam_dir / "vectors.ids.i64").read_bytes()
    original_meta = (seam_dir / "vectors.meta.json").read_text()

    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None

    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(result.archive_path, dest_dir=dest, checksum_path=result.checksum_path)
    assert ok is True
    assert (dest / "vectors.f32").read_bytes() == original_vectors
    assert (dest / "vectors.ids.i64").read_bytes() == original_ids
    assert (dest / "vectors.meta.json").read_text() == original_meta


def test_unpack_returns_true_on_success(tmp_path: Path) -> None:
    """unpack_index returns True on a successful extraction."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(result.archive_path, dest_dir=dest, checksum_path=result.checksum_path)
    assert ok is True


# ── Unpack: checksum rejection ─────────────────────────────────────────────────


def test_unpack_rejects_wrong_checksum(tmp_path: Path) -> None:
    """unpack_index returns False and extracts nothing when the checksum is wrong."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    # Corrupt the checksum sidecar
    result.checksum_path.write_text("deadbeef" * 8 + "  seam-index.tar.gz\n")

    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(result.archive_path, dest_dir=dest, checksum_path=result.checksum_path)
    assert ok is False
    # Nothing must have been extracted
    assert not (dest / "seam.db").exists()


def test_unpack_without_checksum_still_extracts(tmp_path: Path) -> None:
    """unpack_index with no checksum_path (None) extracts successfully (no verification)."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None
    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(result.archive_path, dest_dir=dest)  # no checksum_path
    assert ok is True
    assert (dest / "seam.db").exists()


# ── Unpack: path-traversal guard ──────────────────────────────────────────────


def test_unpack_rejects_absolute_path_member(tmp_path: Path) -> None:
    """unpack_index returns False and extracts nothing for a member with absolute path."""
    # Build a malicious archive with an absolute path entry
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = b"evil content"
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(archive, dest_dir=dest)
    assert ok is False
    # The attack file must not exist on disk
    assert not Path("/tmp/evil-test-seam").exists()  # sanity


def test_unpack_rejects_dotdot_path_traversal(tmp_path: Path) -> None:
    """unpack_index returns False and extracts nothing for a member with .. components."""
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = b"evil traversal"
        info = tarfile.TarInfo(name="../../etc/evil-seam-test")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(archive, dest_dir=dest)
    assert ok is False


def test_unpack_rejects_nested_dotdot(tmp_path: Path) -> None:
    """unpack_index rejects a member where .. appears as an interior path component."""
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = b"x"
        info = tarfile.TarInfo(name="subdir/../../../evil")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(archive, dest_dir=dest)
    assert ok is False


def test_unpack_nothing_written_when_traversal_detected(tmp_path: Path) -> None:
    """When any member fails the path-traversal guard, NO files are extracted (all-or-nothing)."""
    seam_dir = _make_seam_dir(tmp_path)
    result = pack_index(seam_dir, dest_dir=tmp_path / "out")
    assert result is not None

    # Build a mixed archive: one good member + one malicious member
    mixed_archive = tmp_path / "mixed.tar.gz"
    with tarfile.open(mixed_archive, "w:gz") as tf:
        # Good member
        good_data = b"legitimate seam.db"
        good_info = tarfile.TarInfo(name="seam.db")
        good_info.size = len(good_data)
        tf.addfile(good_info, io.BytesIO(good_data))
        # Bad member
        bad_data = b"evil"
        bad_info = tarfile.TarInfo(name="../evil-seam-test")
        bad_info.size = len(bad_data)
        tf.addfile(bad_info, io.BytesIO(bad_data))

    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(mixed_archive, dest_dir=dest)
    assert ok is False
    # The good member must NOT have been extracted either (all-or-nothing)
    assert not (dest / "seam.db").exists()


# ── Unpack: symlink/hardlink guard ───────────────────────────────────────────


def test_unpack_rejects_symlink_member(tmp_path: Path) -> None:
    """unpack_index returns False and extracts nothing when a member is a symlink.

    Symlinks whose linkname escapes the destination are a classic path-traversal
    vector that name-only checks miss.  Our canonical archives never contain links,
    so any symlink member means the archive is corrupt or malicious.
    """
    archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        # Create a symlink member pointing to an arbitrary location.
        link_info = tarfile.TarInfo(name="evil-link")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = "/etc/passwd"
        link_info.size = 0
        tf.addfile(link_info)

    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(archive, dest_dir=dest)
    assert ok is False
    # Nothing must have been extracted
    assert not list(dest.iterdir()), "Symlink member must not be extracted"


def test_unpack_rejects_hardlink_member(tmp_path: Path) -> None:
    """unpack_index returns False and extracts nothing when a member is a hardlink."""
    archive = tmp_path / "hardlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        link_info = tarfile.TarInfo(name="evil-hardlink")
        link_info.type = tarfile.LNKTYPE
        link_info.linkname = "/etc/shadow"
        link_info.size = 0
        tf.addfile(link_info)

    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(archive, dest_dir=dest)
    assert ok is False
    assert not list(dest.iterdir()), "Hardlink member must not be extracted"


# ── Unpack: corrupt archive ───────────────────────────────────────────────────


def test_unpack_corrupt_archive_returns_false(tmp_path: Path) -> None:
    """unpack_index returns False (never raises) for a corrupt / truncated archive."""
    corrupt = tmp_path / "corrupt.tar.gz"
    corrupt.write_bytes(b"not a valid gzip stream at all")
    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(corrupt, dest_dir=dest)
    assert ok is False


def test_unpack_missing_archive_returns_false(tmp_path: Path) -> None:
    """unpack_index returns False (never raises) when the archive file is absent."""
    dest = tmp_path / "dest"
    dest.mkdir()
    ok = unpack_index(tmp_path / "nonexistent.tar.gz", dest_dir=dest)
    assert ok is False


# ── Config knob ───────────────────────────────────────────────────────────────


def test_seam_index_artifact_url_knob_exists() -> None:
    """SEAM_INDEX_ARTIFACT_URL is defined in seam.config and defaults to empty string."""
    from seam import config

    assert hasattr(config, "SEAM_INDEX_ARTIFACT_URL")
    # Default value must be empty string (feature inert when unset)
    import os

    if "SEAM_INDEX_ARTIFACT_URL" not in os.environ:
        assert config.SEAM_INDEX_ARTIFACT_URL == ""
