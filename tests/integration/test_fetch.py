"""Integration tests for WS4 S3 — seam fetch orchestration (issue #249).

All tests are OFFLINE: artifact downloads use file:// URLs pointing to locally
packed archives. Real git repos are created in tmp_path so SHA resolution works
without any network access.

Test groups:
    FT1 — Unset SEAM_INDEX_ARTIFACT_URL gives clear guidance error; no .seam/ created
    FT2 — Non-git directory gives NOT_A_GIT_REPO error; no .seam/ created
    FT3 — Happy path: download, verify, unpack, rebase, sync; queries return local paths
    FT4 — Nearest-ancestor fallback: fetches closest published ancestor when HEAD has no artifact
    FT5 — Atomic swap-in: corrupt checksum leaves existing .seam/ byte-for-byte intact
    FT6 — Atomic swap-in: missing archive leaves existing .seam/ intact
    FT7 — Idempotent: running seam fetch twice converges to same state
    FT8 — --json output is a valid envelope with expected keys
    FT9 — --quiet output is key:value pairs, one per line
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import seam.config as config
from seam.cli.fetch import FetchError, fetch_index
from seam.cli.main import app
from seam.indexer.artifact import ARCHIVE_FILENAME, CHECKSUM_FILENAME, pack_index
from seam.indexer.db import connect
from seam.indexer.init_index import run_init
from seam.query.engine import query

# ── Test fixtures & helpers ────────────────────────────────────────────────────


def _git_init_with_commit(repo_dir: Path, files: dict[str, str]) -> str:
    """Create a git repo with the given files and one commit. Returns HEAD SHA."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    for name, content in files.items():
        (repo_dir / name).write_text(content)
        subprocess.run(
            ["git", "-C", str(repo_dir), "add", name],
            check=True, capture_output=True,
        )
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "initial"],
        check=True, capture_output=True,
    )
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _build_artifact(repo_dir: Path, artifacts_dir: Path, sha: str) -> Path:
    """Run seam init in repo_dir, pack the index, and store it under artifacts_dir/sha/."""
    run_init(repo_dir)
    seam_dir = repo_dir / ".seam"
    out_dir = artifacts_dir / sha
    out_dir.mkdir(parents=True, exist_ok=True)
    result = pack_index(seam_dir, dest_dir=out_dir)
    assert result is not None, "pack_index should succeed"
    return out_dir


def _artifact_url_template(artifacts_dir: Path) -> str:
    """Return a file:// URL template for serving artifacts from artifacts_dir.

    WHY not use Path.as_uri() with '{sha}' as a directory: Path.as_uri() percent-encodes
    curly braces ({→%7B, }→%7D), which breaks the replace('{sha}', sha) substitution.
    We must construct the template as a plain string after converting the base dir.
    """
    base_uri = artifacts_dir.as_uri()  # file:///path/to/artifacts
    return f"{base_uri}/{{sha}}/{ARCHIVE_FILENAME}"


def _checksum_url_for(archive_url: str) -> str:
    """Derive the checksum URL from the archive URL (replace ARCHIVE_FILENAME)."""
    return archive_url.replace(ARCHIVE_FILENAME, CHECKSUM_FILENAME)


def _sentinel_content(sentinel_dir: Path) -> dict[str, bytes]:
    """Snapshot all files in sentinel_dir as {relative_name: bytes}."""
    snapshot: dict[str, bytes] = {}
    if not sentinel_dir.exists():
        return snapshot
    for f in sorted(sentinel_dir.rglob("*")):
        if f.is_file():
            snapshot[str(f.relative_to(sentinel_dir))] = f.read_bytes()
    return snapshot


# ── FT1: Unset URL gives guidance error ───────────────────────────────────────


class TestUnsetUrl:
    """FT1 — SEAM_INDEX_ARTIFACT_URL not set."""

    def test_unset_url_raises_fetch_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FT1a: When URL template is empty, FetchError with INVALID_INPUT is raised."""
        # Create a valid git repo so the git check doesn't fail first
        _git_init_with_commit(tmp_path, {"hello.py": "def hello(): pass\n"})
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", "")

        with pytest.raises(FetchError) as exc_info:
            fetch_index(tmp_path)

        assert exc_info.value.code == "INVALID_INPUT"
        assert "SEAM_INDEX_ARTIFACT_URL" in exc_info.value.message

    def test_unset_url_leaves_no_seam_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FT1b: When URL is unset, .seam/ is never created."""
        _git_init_with_commit(tmp_path, {"hello.py": "def hello(): pass\n"})
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", "")

        with pytest.raises(FetchError):
            fetch_index(tmp_path)

        assert not (tmp_path / ".seam").exists()


# ── FT2: Non-git directory gives NOT_A_GIT_REPO error ────────────────────────


class TestNonGitDir:
    """FT2 — Not a git repository."""

    def test_non_git_dir_raises_fetch_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FT2a: A plain directory (not a git repo) raises FetchError with NOT_A_GIT_REPO."""
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", "https://example.com/{sha}/seam-index.tar.gz")

        with pytest.raises(FetchError) as exc_info:
            fetch_index(tmp_path)

        assert exc_info.value.code == "NOT_A_GIT_REPO"
        assert "seam init" in exc_info.value.message.lower()

    def test_non_git_dir_leaves_no_seam_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FT2b: On NOT_A_GIT_REPO, no .seam/ is created."""
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", "https://example.com/{sha}/seam-index.tar.gz")

        with pytest.raises(FetchError):
            fetch_index(tmp_path)

        assert not (tmp_path / ".seam").exists()


# ── FT3: Happy path end-to-end ────────────────────────────────────────────────


class TestHappyPath:
    """FT3 — Full fetch cycle: download → verify → unpack → rebase → sync."""

    def test_happy_path_creates_seam_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT3a: After fetch_index, .seam/seam.db is present."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        sha = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})

        _build_artifact(repo_dir, artifacts_dir, sha)

        # Remove .seam so fetch starts fresh
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        url_template = _artifact_url_template(artifacts_dir)
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", url_template)

        fetch_index(repo_dir)

        assert (repo_dir / ".seam" / "seam.db").is_file()

    def test_happy_path_returns_correct_sha(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT3b: Result contains the resolved SHA (HEAD)."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        sha = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        _build_artifact(repo_dir, artifacts_dir, sha)
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", _artifact_url_template(artifacts_dir))

        result = fetch_index(repo_dir)

        assert result["sha"] == sha

    def test_happy_path_returns_bytes_downloaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT3c: Result contains bytes_downloaded > 0."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        sha = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        _build_artifact(repo_dir, artifacts_dir, sha)
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", _artifact_url_template(artifacts_dir))

        result = fetch_index(repo_dir)

        assert result["bytes_downloaded"] > 0

    def test_happy_path_queries_return_local_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT3d: After fetch + rebase, query() returns symbols with local paths.

        This is the KEY observable behavior: the index was built on 'repo_dir',
        packed, then fetched into the same dir (simulating CI → dev). After rebase,
        file paths in the DB should point to repo_dir on the local machine.
        """
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        sha = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        _build_artifact(repo_dir, artifacts_dir, sha)
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", _artifact_url_template(artifacts_dir))

        fetch_index(repo_dir)

        db_path = config.get_db_path(repo_dir)
        conn = connect(db_path)
        try:
            results = query(conn, "greet")
        finally:
            conn.close()

        # There should be at least one result matching the 'greet' function
        assert len(results) > 0
        # All file paths should be under repo_dir (not some foreign CI path)
        for row in results:
            assert str(repo_dir) in row["file"], (
                f"Expected local path under {repo_dir}, got {row['file']}"
            )

    def test_happy_path_result_has_sync_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT3e: Result dict includes sync sub-dict with standard sync keys."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        sha = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        _build_artifact(repo_dir, artifacts_dir, sha)
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", _artifact_url_template(artifacts_dir))

        result = fetch_index(repo_dir)

        assert "sync" in result
        sync = result["sync"]
        for key in ("added", "modified", "removed", "unchanged"):
            assert key in sync, f"Missing '{key}' in sync result"


# ── FT4: Nearest-ancestor fallback ────────────────────────────────────────────


class TestAncestorFallback:
    """FT4 — When HEAD has no artifact, fall back to the nearest published ancestor."""

    def test_ancestor_fallback_uses_parent_sha(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT4a: When HEAD artifact is absent but parent's artifact exists, parent SHA is used."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"

        # Commit 1: build and pack the artifact
        sha1 = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        _build_artifact(repo_dir, artifacts_dir, sha1)
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        # Commit 2: add a second commit (HEAD now = sha2, artifact NOT published)
        (repo_dir / "extra.py").write_text("def extra(): pass\n")
        subprocess.run(["git", "-C", str(repo_dir), "add", "extra.py"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-m", "second commit"],
            check=True, capture_output=True,
        )
        sha2_result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        sha2 = sha2_result.stdout.strip()
        assert sha2 != sha1, "SHA should differ after second commit"

        url_template = _artifact_url_template(artifacts_dir)
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", url_template)

        # Fetch should fall back to sha1 (the published ancestor)
        result = fetch_index(repo_dir)

        assert result["sha"] == sha1, (
            f"Expected fallback to sha1={sha1[:8]}, got {result['sha'][:8]}"
        )

    def test_ancestor_fallback_creates_working_index(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT4b: The ancestor-fallback fetch still creates a queryable index."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"

        sha1 = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        _build_artifact(repo_dir, artifacts_dir, sha1)
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        # Add second commit (no artifact for sha2)
        (repo_dir / "extra.py").write_text("def extra(): pass\n")
        subprocess.run(["git", "-C", str(repo_dir), "add", "extra.py"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-m", "second"],
            check=True, capture_output=True,
        )

        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", _artifact_url_template(artifacts_dir))

        fetch_index(repo_dir)

        # Index should be present and queryable
        assert (repo_dir / ".seam" / "seam.db").is_file()
        db_path = config.get_db_path(repo_dir)
        conn = connect(db_path)
        try:
            results = query(conn, "greet")
        finally:
            conn.close()
        assert len(results) > 0

    def test_no_published_ancestor_raises_fetch_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT4c: When no ancestor has an artifact (depth exhausted), FetchError is raised."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        # No artifact published for any SHA
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        url_template = _artifact_url_template(artifacts_dir)
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", url_template)
        monkeypatch.setattr(config, "SEAM_FETCH_ANCESTOR_DEPTH", 5)

        with pytest.raises(FetchError) as exc_info:
            fetch_index(repo_dir)

        assert exc_info.value.code == "FETCH_FAILED"


# ── FT5: Atomic swap-in: corrupt checksum leaves existing .seam/ intact ───────


class TestAtomicSwapOnFailure:
    """FT5/FT6 — Atomic swap-in contract: existing .seam/ is untouched on any failure."""

    def test_corrupt_checksum_leaves_seam_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT5: A valid archive with a CORRUPT checksum leaves the pre-existing .seam/ intact.

        This is the most important safety property: corrupt/untrusted artifacts
        must NEVER damage an existing index.
        """
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        sha = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})

        # Build and pack the artifact (creates valid archive + checksum)
        out_dir = _build_artifact(repo_dir, artifacts_dir, sha)

        # NOW corrupt the checksum file so verification fails
        checksum_file = out_dir / CHECKSUM_FILENAME
        checksum_file.write_text("0" * 64 + "  " + ARCHIVE_FILENAME + "\n")

        # Create a sentinel .seam/ with a recognizable file
        existing_seam = repo_dir / ".seam"
        shutil.rmtree(existing_seam, ignore_errors=True)
        existing_seam.mkdir()
        sentinel = existing_seam / "SENTINEL.txt"
        sentinel.write_text("original index — must survive")

        # Snapshot the original .seam/ before the failed fetch
        snapshot_before = _sentinel_content(existing_seam)

        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", _artifact_url_template(artifacts_dir))

        with pytest.raises(FetchError):
            fetch_index(repo_dir)

        # The original .seam/ must be byte-for-byte identical
        snapshot_after = _sentinel_content(existing_seam)
        assert snapshot_after == snapshot_before, (
            "Existing .seam/ was modified by a failed fetch (atomic swap violated)"
        )

    def test_missing_archive_leaves_seam_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT6: A 404 (missing archive) leaves the pre-existing .seam/ intact."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})

        # Do NOT build any artifact — artifacts_dir is empty (all URLs will 404)
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Create sentinel .seam/
        existing_seam = repo_dir / ".seam"
        shutil.rmtree(existing_seam, ignore_errors=True)
        existing_seam.mkdir()
        (existing_seam / "SENTINEL.txt").write_text("must survive")

        snapshot_before = _sentinel_content(existing_seam)

        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", _artifact_url_template(artifacts_dir))
        monkeypatch.setattr(config, "SEAM_FETCH_ANCESTOR_DEPTH", 2)

        with pytest.raises(FetchError):
            fetch_index(repo_dir)

        # Original .seam/ must be untouched
        snapshot_after = _sentinel_content(existing_seam)
        assert snapshot_after == snapshot_before, (
            "Existing .seam/ was modified when artifact was missing (atomic swap violated)"
        )

    def test_no_seam_dir_on_failure_when_none_existed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT6b: When no .seam/ existed before the failed fetch, none is left behind."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        # No artifacts published
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Ensure no .seam/ initially
        assert not (repo_dir / ".seam").exists()

        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", _artifact_url_template(artifacts_dir))
        monkeypatch.setattr(config, "SEAM_FETCH_ANCESTOR_DEPTH", 2)

        with pytest.raises(FetchError):
            fetch_index(repo_dir)

        # No .seam/ should have been left behind
        assert not (repo_dir / ".seam").exists(), ".seam/ created by a failed fetch (should not exist)"


# ── FT7: Idempotency ─────────────────────────────────────────────────────────


class TestIdempotency:
    """FT7 — Running seam fetch twice converges to the same state."""

    def test_second_fetch_succeeds_and_is_consistent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT7: Two consecutive fetches on the same SHA both succeed."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        sha = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        _build_artifact(repo_dir, artifacts_dir, sha)
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", _artifact_url_template(artifacts_dir))

        result1 = fetch_index(repo_dir)
        result2 = fetch_index(repo_dir)

        # Both fetches should resolve to the same SHA
        assert result1["sha"] == result2["sha"] == sha

        # The index should still be queryable after the second fetch
        db_path = config.get_db_path(repo_dir)
        conn = connect(db_path)
        try:
            results = query(conn, "greet")
        finally:
            conn.close()
        assert len(results) > 0


# ── FT8: --json output mode ───────────────────────────────────────────────────


class TestJsonOutput:
    """FT8 — --json CLI flag emits a valid envelope."""

    def test_json_output_is_valid_envelope(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT8: seam fetch --json emits {ok:true, data:{sha, bytes_downloaded, ...}}."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        sha = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        _build_artifact(repo_dir, artifacts_dir, sha)
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        url_template = _artifact_url_template(artifacts_dir)
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", url_template)

        runner = CliRunner()
        result = runner.invoke(app, ["fetch", str(repo_dir), "--json"])

        assert result.exit_code == 0, f"seam fetch --json failed: {result.output}"
        envelope = json.loads(result.output.strip())
        assert envelope["ok"] is True
        data = envelope["data"]
        assert "sha" in data
        assert "bytes_downloaded" in data
        assert "files_rebased" in data
        assert "sync" in data

    def test_json_error_on_missing_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT8b: seam fetch --json with no URL emits {ok:false, error:{code, message}}."""
        _git_init_with_commit(tmp_path, {"x.py": "x = 1\n"})
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", "")

        runner = CliRunner()
        result = runner.invoke(app, ["fetch", str(tmp_path), "--json"])

        # Must exit non-zero
        assert result.exit_code != 0
        envelope = json.loads(result.output.strip())
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "INVALID_INPUT"


# ── FT9: --quiet output mode ──────────────────────────────────────────────────


class TestQuietOutput:
    """FT9 — --quiet CLI flag emits bare key:value lines."""

    def test_quiet_output_contains_sha(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT9: seam fetch --quiet emits 'sha: <sha>' line."""
        artifacts_dir = tmp_path / "artifacts"
        repo_dir = tmp_path / "repo"
        sha = _git_init_with_commit(repo_dir, {"module.py": "def greet(): return 'hello'\n"})
        _build_artifact(repo_dir, artifacts_dir, sha)
        shutil.rmtree(repo_dir / ".seam", ignore_errors=True)

        url_template = _artifact_url_template(artifacts_dir)
        monkeypatch.setattr(config, "SEAM_INDEX_ARTIFACT_URL", url_template)

        runner = CliRunner()
        result = runner.invoke(app, ["fetch", str(repo_dir), "--quiet"])

        assert result.exit_code == 0, f"seam fetch --quiet failed: {result.output}"
        # Should contain a sha line
        assert f"sha: {sha}" in result.output

    def test_json_and_quiet_are_mutually_exclusive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FT9b: --json and --quiet together are rejected."""
        _git_init_with_commit(tmp_path, {"x.py": "x = 1\n"})
        monkeypatch.setattr(
            config, "SEAM_INDEX_ARTIFACT_URL", "https://example.com/{sha}/seam-index.tar.gz"
        )

        runner = CliRunner()
        result = runner.invoke(app, ["fetch", str(tmp_path), "--json", "--quiet"])

        # Must exit non-zero (mutual exclusion error)
        assert result.exit_code != 0
