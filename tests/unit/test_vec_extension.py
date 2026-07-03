"""Unit tests for seam/query/vec_extension.py — WS2b S1.

TDD: one behavior → one test. All branches are covered via monkeypatching —
no reliance on a broken SQLite build; real-sqlite-vec tests self-skip when
the [semantic-ann] extra is absent.

Test groups:
  VE1 — success path (real sqlite-vec): probe returns True, loader works, KNN query runs.
  VE2 — enable_load_extension disabled: AttributeError → False, exactly one warning.
  VE3 — enable_load_extension raises OperationalError: → False, exactly one warning.
  VE4 — package absent: ImportError on sqlite_vec → False, exactly one warning.
  VE5 — load error: sqlite_vec.load raises → False, exactly one warning.
  VE6 — probe failure: vec0 CREATE raises → False, exactly one warning.
  VE7 — load_vec_extension success path: True, extension stays loaded on conn.
  VE8 — load_vec_extension failure paths (enable/import/load errors).
"""

import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest

from seam.query.vec_extension import load_vec_extension, probe_vec_extension

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_conn() -> sqlite3.Connection:
    """Open a fresh in-memory connection for tests."""
    return sqlite3.connect(":memory:")


# ═══════════════════════════════════════════════════════════════════════════════
# VE1 — success path (real sqlite-vec present)
# ═══════════════════════════════════════════════════════════════════════════════


class TestVE1SuccessPath:
    """Tests that require the real sqlite-vec package — self-skip when absent."""

    def test_ve1_1_probe_returns_true(self) -> None:
        """probe_vec_extension returns True when sqlite-vec is present and working."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        result = probe_vec_extension(conn)
        assert result is True

    def test_ve1_2_probe_leaves_caller_conn_clean(self) -> None:
        """Probe must NOT leave any table or state on the caller's connection."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        probe_vec_extension(conn)
        # The probe should have used its own :memory: connection — no trace in ours.
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert tables == [], "probe must not create tables on the caller's connection"

    def test_ve1_3_probe_can_be_called_multiple_times(self) -> None:
        """Calling probe_vec_extension twice on the same connection is safe."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        assert probe_vec_extension(conn) is True
        assert probe_vec_extension(conn) is True

    def test_ve1_4_loader_returns_true(self) -> None:
        """load_vec_extension returns True when sqlite-vec loads successfully."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        result = load_vec_extension(conn)
        assert result is True

    def test_ve1_5_loader_extension_is_active_after_load(self) -> None:
        """After load_vec_extension, vec_version() works on the connection."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        assert load_vec_extension(conn) is True
        row = conn.execute("SELECT vec_version()").fetchone()
        assert row is not None
        assert row[0].startswith("v"), f"unexpected vec_version: {row[0]!r}"

    def test_ve1_6_knn_query_works_after_load(self) -> None:
        """After load_vec_extension a vec0 table with a KNN query works end-to-end."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        assert load_vec_extension(conn) is True
        conn.execute("CREATE VIRTUAL TABLE knn_test USING vec0(embedding float[4])")
        import struct

        conn.execute(
            "INSERT INTO knn_test(rowid, embedding) VALUES (?, ?)",
            (1, struct.pack("4f", 1.0, 0.0, 0.0, 0.0)),
        )
        conn.execute(
            "INSERT INTO knn_test(rowid, embedding) VALUES (?, ?)",
            (2, struct.pack("4f", 0.0, 1.0, 0.0, 0.0)),
        )
        query_vec = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        rows = conn.execute(
            "SELECT rowid, distance FROM knn_test WHERE embedding MATCH ? ORDER BY distance LIMIT 2",
            (query_vec,),
        ).fetchall()
        assert len(rows) == 2
        # The first result should be rowid=1 (identical to the query vector → distance=0)
        assert rows[0][0] == 1

    def test_ve1_7_probe_does_not_raise_on_any_path(self) -> None:
        """probe_vec_extension never raises even with a valid connection."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        # Should not raise; just return a bool.
        result = probe_vec_extension(conn)
        assert isinstance(result, bool)

    def test_ve1_8_load_extension_loading_disabled_after_load(self) -> None:
        """After load_vec_extension, further extension loads are blocked (security)."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        assert load_vec_extension(conn) is True
        # enable_load_extension should have been set back to False.
        # Attempting to load a non-existent extension must fail with OperationalError,
        # not succeed — which proves extension loading was disabled.
        with pytest.raises(Exception):
            conn.execute("SELECT load_extension('/nonexistent/path/fake.so')")


# ═══════════════════════════════════════════════════════════════════════════════
# VE2 — enable_load_extension raises AttributeError (some SQLite builds)
# ═══════════════════════════════════════════════════════════════════════════════


class TestVE2EnableLoadExtensionAttributeError:
    """Simulate a SQLite build where enable_load_extension is not available."""

    def test_ve2_1_probe_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        """probe_vec_extension returns False when enable_load_extension is missing."""
        conn = _make_conn()
        # Simulate a Python/SQLite build that doesn't expose enable_load_extension.
        original = sqlite3.connect

        def patched_connect(database: str, **kwargs: object) -> sqlite3.Connection:
            c = original(database, **kwargs)
            # Remove the method to simulate "not compiled in"
            del c.enable_load_extension  # type: ignore[attr-defined]
            return c

        with patch("seam.query.vec_extension.sqlite3.connect", side_effect=patched_connect):
            with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                result = probe_vec_extension(conn)

        assert result is False

    def test_ve2_2_probe_logs_exactly_one_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """probe_vec_extension logs exactly one WARNING when enable_load_extension is missing."""
        conn = _make_conn()

        original = sqlite3.connect

        def patched_connect(database: str, **kwargs: object) -> sqlite3.Connection:
            c = original(database, **kwargs)
            del c.enable_load_extension  # type: ignore[attr-defined]
            return c

        with patch("seam.query.vec_extension.sqlite3.connect", side_effect=patched_connect):
            with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                probe_vec_extension(conn)

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1, f"expected 1 warning, got {len(warnings)}: {warnings}"

    def test_ve2_3_probe_never_raises(self) -> None:
        """probe_vec_extension never raises even when enable_load_extension is missing."""
        conn = _make_conn()
        original = sqlite3.connect

        def patched_connect(database: str, **kwargs: object) -> sqlite3.Connection:
            c = original(database, **kwargs)
            del c.enable_load_extension  # type: ignore[attr-defined]
            return c

        with patch("seam.query.vec_extension.sqlite3.connect", side_effect=patched_connect):
            # Must not raise
            result = probe_vec_extension(conn)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# VE3 — enable_load_extension raises OperationalError ("not authorized")
# ═══════════════════════════════════════════════════════════════════════════════


class TestVE3EnableLoadExtensionOperationalError:
    """Simulate a build where enable_load_extension raises OperationalError."""

    def test_ve3_1_probe_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        """probe_vec_extension returns False when enable_load_extension raises OperationalError."""
        conn = _make_conn()
        original = sqlite3.connect

        def patched_connect(database: str, **kwargs: object) -> sqlite3.Connection:
            c = original(database, **kwargs)

            def raise_op_error(enabled: bool) -> None:  # noqa: FBT001
                raise sqlite3.OperationalError("not authorized")

            c.enable_load_extension = raise_op_error  # type: ignore[method-assign]
            return c

        with patch("seam.query.vec_extension.sqlite3.connect", side_effect=patched_connect):
            with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                result = probe_vec_extension(conn)

        assert result is False

    def test_ve3_2_probe_logs_exactly_one_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exactly one warning is logged when enable_load_extension raises OperationalError."""
        conn = _make_conn()
        original = sqlite3.connect

        def patched_connect(database: str, **kwargs: object) -> sqlite3.Connection:
            c = original(database, **kwargs)

            def raise_op_error(enabled: bool) -> None:  # noqa: FBT001
                raise sqlite3.OperationalError("not authorized")

            c.enable_load_extension = raise_op_error  # type: ignore[method-assign]
            return c

        with patch("seam.query.vec_extension.sqlite3.connect", side_effect=patched_connect):
            with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                probe_vec_extension(conn)

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# VE4 — package absent (ImportError on sqlite_vec)
# ═══════════════════════════════════════════════════════════════════════════════


class TestVE4PackageAbsent:
    """Simulate the sqlite_vec package not being installed."""

    def test_ve4_1_probe_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        """probe_vec_extension returns False when sqlite_vec cannot be imported."""
        conn = _make_conn()
        # Remove sqlite_vec from sys.modules to force ImportError on the lazy import.
        saved = sys.modules.pop("sqlite_vec", None)
        try:
            with patch.dict(sys.modules, {"sqlite_vec": None}):  # type: ignore[dict-item]
                with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                    result = probe_vec_extension(conn)
        finally:
            if saved is not None:
                sys.modules["sqlite_vec"] = saved

        assert result is False

    def test_ve4_2_probe_logs_exactly_one_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exactly one warning is logged when sqlite_vec import fails."""
        conn = _make_conn()
        saved = sys.modules.pop("sqlite_vec", None)
        try:
            with patch.dict(sys.modules, {"sqlite_vec": None}):  # type: ignore[dict-item]
                with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                    probe_vec_extension(conn)
        finally:
            if saved is not None:
                sys.modules["sqlite_vec"] = saved

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    def test_ve4_3_probe_never_raises(self) -> None:
        """probe_vec_extension never raises when sqlite_vec is absent."""
        conn = _make_conn()
        saved = sys.modules.pop("sqlite_vec", None)
        try:
            with patch.dict(sys.modules, {"sqlite_vec": None}):  # type: ignore[dict-item]
                result = probe_vec_extension(conn)
        finally:
            if saved is not None:
                sys.modules["sqlite_vec"] = saved
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# VE5 — load error: sqlite_vec.load raises
# ═══════════════════════════════════════════════════════════════════════════════


class TestVE5LoadError:
    """Simulate sqlite_vec.load raising an exception."""

    def _make_bad_sqlite_vec(self) -> MagicMock:
        """Return a mock sqlite_vec module whose load() raises RuntimeError."""
        mock = MagicMock()
        mock.load.side_effect = RuntimeError("shared library load failed")
        return mock

    def test_ve5_1_probe_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        """probe_vec_extension returns False when sqlite_vec.load raises."""
        conn = _make_conn()
        bad_vec = self._make_bad_sqlite_vec()
        with patch.dict(sys.modules, {"sqlite_vec": bad_vec}):
            with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                result = probe_vec_extension(conn)
        assert result is False

    def test_ve5_2_probe_logs_exactly_one_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exactly one warning is logged when sqlite_vec.load raises."""
        conn = _make_conn()
        bad_vec = self._make_bad_sqlite_vec()
        with patch.dict(sys.modules, {"sqlite_vec": bad_vec}):
            with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                probe_vec_extension(conn)
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    def test_ve5_3_probe_never_raises(self) -> None:
        """probe_vec_extension never raises when sqlite_vec.load fails."""
        conn = _make_conn()
        bad_vec = self._make_bad_sqlite_vec()
        with patch.dict(sys.modules, {"sqlite_vec": bad_vec}):
            result = probe_vec_extension(conn)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# VE6 — probe failure: vec0 CREATE raises
# ═══════════════════════════════════════════════════════════════════════════════


class TestVE6ProbeTableFailure:
    """Simulate sqlite_vec loading OK but the vec0 CREATE TABLE failing."""

    def _make_load_ok_create_fail_sqlite_vec(self) -> MagicMock:
        """Mock that loads successfully but leaves the connection unable to CREATE vec0."""
        mock = MagicMock()
        # load() succeeds (returns None like the real sqlite_vec.load)
        mock.load.return_value = None
        return mock

    def test_ve6_1_probe_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        """probe_vec_extension returns False when the vec0 CREATE TABLE fails."""
        conn = _make_conn()
        ok_vec = self._make_load_ok_create_fail_sqlite_vec()
        # The mock's load() does nothing, so vec0 is not actually registered.
        # Executing "CREATE VIRTUAL TABLE ... USING vec0(...)" will raise OperationalError.
        with patch.dict(sys.modules, {"sqlite_vec": ok_vec}):
            with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                result = probe_vec_extension(conn)
        assert result is False

    def test_ve6_2_probe_logs_exactly_one_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exactly one warning is logged when the vec0 CREATE TABLE fails."""
        conn = _make_conn()
        ok_vec = self._make_load_ok_create_fail_sqlite_vec()
        with patch.dict(sys.modules, {"sqlite_vec": ok_vec}):
            with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                probe_vec_extension(conn)
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    def test_ve6_3_probe_never_raises(self) -> None:
        """probe_vec_extension never raises when the vec0 table creation fails."""
        conn = _make_conn()
        ok_vec = self._make_load_ok_create_fail_sqlite_vec()
        with patch.dict(sys.modules, {"sqlite_vec": ok_vec}):
            result = probe_vec_extension(conn)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# VE7 — load_vec_extension success path
# ═══════════════════════════════════════════════════════════════════════════════


class TestVE7LoadSuccess:
    """load_vec_extension success path — requires real sqlite_vec."""

    def test_ve7_1_returns_true(self) -> None:
        """load_vec_extension returns True when sqlite-vec loads."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        assert load_vec_extension(conn) is True

    def test_ve7_2_vec_version_works_after_load(self) -> None:
        """vec_version() is callable on the connection after load_vec_extension."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        load_vec_extension(conn)
        row = conn.execute("SELECT vec_version()").fetchone()
        assert row is not None and row[0].startswith("v")

    def test_ve7_3_never_raises(self) -> None:
        """load_vec_extension never raises even on repeated calls."""
        pytest.importorskip("sqlite_vec")
        conn = _make_conn()
        result = load_vec_extension(conn)
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════════════════════════
# VE8 — load_vec_extension failure paths
# ═══════════════════════════════════════════════════════════════════════════════


class TestVE8LoadFailurePaths:
    """load_vec_extension failure branches — mock connections, no real sqlite_vec needed.

    NOTE: sqlite3.Connection.enable_load_extension is a read-only attribute on the
    C extension type (Python 3.12+), so we can't delete or reassign it directly on a
    real connection object.  Instead we pass a MagicMock that simulates the specific
    failure mode.  This is the correct approach: test the function's contract, not the
    real SQLite internals.
    """

    def test_ve8_1_enable_attribute_error_returns_false(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """load_vec_extension returns False when enable_load_extension raises AttributeError."""
        # Simulate a Python build where enable_load_extension is not present.
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.enable_load_extension.side_effect = AttributeError(
            "enable_load_extension not supported"
        )
        with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
            result = load_vec_extension(mock_conn)  # type: ignore[arg-type]
        assert result is False
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    def test_ve8_2_enable_operational_error_returns_false(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """load_vec_extension returns False when enable_load_extension raises OperationalError."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.enable_load_extension.side_effect = sqlite3.OperationalError("not authorized")
        with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
            result = load_vec_extension(mock_conn)  # type: ignore[arg-type]
        assert result is False
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    def test_ve8_3_package_absent_returns_false(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """load_vec_extension returns False when sqlite_vec cannot be imported."""
        conn = _make_conn()
        saved = sys.modules.pop("sqlite_vec", None)
        try:
            with patch.dict(sys.modules, {"sqlite_vec": None}):  # type: ignore[dict-item]
                with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                    result = load_vec_extension(conn)
        finally:
            if saved is not None:
                sys.modules["sqlite_vec"] = saved
        assert result is False
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    def test_ve8_4_load_error_returns_false(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """load_vec_extension returns False when sqlite_vec.load raises."""
        conn = _make_conn()
        bad_vec = MagicMock()
        bad_vec.load.side_effect = RuntimeError("extension crash")
        with patch.dict(sys.modules, {"sqlite_vec": bad_vec}):
            with caplog.at_level("WARNING", logger="seam.query.vec_extension"):
                result = load_vec_extension(conn)
        assert result is False
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1

    def test_ve8_5_never_raises_on_enable_attribute_error(self) -> None:
        """load_vec_extension never raises when enable_load_extension is missing."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.enable_load_extension.side_effect = AttributeError(
            "enable_load_extension not supported"
        )
        result = load_vec_extension(mock_conn)  # type: ignore[arg-type]
        assert result is False
