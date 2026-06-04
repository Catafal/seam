"""Unit tests for seam/analysis/embeddings.py (T3).

TDD: Tests written BEFORE implementation (RED phase).

All tests are GATE-SAFE: fully offline, no network, no model download.
Synthetic float32 vectors are created with struct.pack (no numpy import needed).
Real-model tests are behind pytest.importorskip("fastembed") so they SKIP when
fastembed is absent (it IS absent in the gate environment).

Test groups:
    E1 — symbol_text: canonical text construction (pure logic, no model).
    E2 — is_available: returns False when fastembed absent; never raises.
    E3 — embed_texts degradation: returns [] when fastembed absent.
    E4 — embed_query degradation: returns empty bytes when fastembed absent.
    E5 — Real model tests (behind importorskip — skipped in gate).
"""

import struct
from unittest.mock import MagicMock, patch

import pytest


def _make_float32_bytes(values: list[float]) -> bytes:
    """Create float32 bytes from a list of floats using struct (no numpy needed)."""
    return struct.pack(f"{len(values)}f", *values)


def _decode_float32_bytes(blob: bytes) -> list[float]:
    """Decode float32 bytes back to a list of floats using struct."""
    n = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{n}f", blob))


# ── E1: symbol_text construction (pure logic) ────────────────────────────────


class TestSymbolText:
    """E1 — symbol_text builds canonical text correctly."""

    def test_symbol_text_with_all_fields(self) -> None:
        """When name, signature, docstring all present, all three are in the result."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text(
            "my_function",
            "def my_function(x: int) -> str",
            "Converts x to string.",
        )
        assert "my_function" in result
        assert "def my_function" in result
        assert "Converts x to string" in result

    def test_symbol_text_no_signature(self) -> None:
        """None signature is gracefully excluded (not injected as 'None' text)."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("MyClass", None, "A useful class.")
        assert "MyClass" in result
        assert "A useful class" in result
        # signature=None must not inject a 'None' line between name and docstring
        lines = result.splitlines()
        assert "None" not in lines

    def test_symbol_text_no_docstring(self) -> None:
        """None docstring is gracefully excluded (not present as a bare 'None' literal)."""
        from seam.analysis.embeddings import symbol_text

        # Use a signature without None in it so we can assert the docstring isn't injected
        result = symbol_text("helper", "def helper() -> int", None)
        assert "helper" in result
        assert "def helper" in result
        # The Python None type annotation is NOT the same as the Python literal "None"
        # being injected as a string — make sure the *docstring* part is absent.
        # We test this by checking the result doesn't end with a trailing "None" line.
        assert not result.endswith("\nNone")

    def test_symbol_text_all_none(self) -> None:
        """When signature and docstring are both None, name is still included.

        The string 'None' must NOT appear as a standalone term from missing fields.
        """
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("orphan", None, None)
        assert "orphan" in result
        # "None" should not appear as a separate word from the missing fields
        assert result == "orphan"  # Only the name — no trailing None lines

    def test_symbol_text_returns_str(self) -> None:
        """Return type is always str."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("x", "sig", "doc")
        assert isinstance(result, str)

    def test_symbol_text_empty_strings(self) -> None:
        """Empty string signature/docstring treated as absent (no empty lines)."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("my_fn", "", "")
        assert "my_fn" in result
        # Empty strings should not contribute extra whitespace-only sections
        assert result.strip() != ""

    def test_symbol_text_consistent(self) -> None:
        """Same inputs always produce identical output (deterministic)."""
        from seam.analysis.embeddings import symbol_text

        a = symbol_text("fn", "def fn()", "Does something.")
        b = symbol_text("fn", "def fn()", "Does something.")
        assert a == b


# ── E2: is_available ──────────────────────────────────────────────────────────


class TestIsAvailable:
    """E2 — is_available() returns False when fastembed is absent; never raises."""

    def test_is_available_returns_bool(self) -> None:
        """is_available always returns a bool, never raises."""
        from seam.analysis.embeddings import is_available

        result = is_available()
        assert isinstance(result, bool)

    def test_is_available_false_when_absent(self) -> None:
        """is_available() returns False when fastembed is not importable.

        This test runs in the gate environment where fastembed IS absent,
        so we verify the function returns False rather than raising.
        """
        import sys

        import seam.analysis.embeddings as emb_mod

        original = sys.modules.get("fastembed")
        # Inject None to simulate absent module
        sys.modules["fastembed"] = None  # type: ignore[assignment]
        emb_mod._fastembed_available = None  # type: ignore[attr-defined]
        try:
            from seam.analysis.embeddings import is_available

            result = is_available()
            assert result is False
        finally:
            if original is None:
                sys.modules.pop("fastembed", None)
            else:
                sys.modules["fastembed"] = original
            emb_mod._fastembed_available = None  # type: ignore[attr-defined]

    def test_is_available_never_raises(self) -> None:
        """is_available() must not raise under any circumstance."""
        from seam.analysis.embeddings import is_available

        try:
            is_available()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"is_available() raised unexpectedly: {exc}")


# ── E3: embed_texts degradation ───────────────────────────────────────────────


class TestEmbedTextsDegradation:
    """E3 — embed_texts returns [] when fastembed is absent; never raises."""

    def test_embed_texts_returns_empty_list_when_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed_texts returns [] when fastembed is not available."""
        monkeypatch.setattr("seam.analysis.embeddings.is_available", lambda: False)

        from seam.analysis.embeddings import embed_texts

        result = embed_texts(["hello world", "foo bar"], "BAAI/bge-small-en-v1.5")
        assert result == []

    def test_embed_texts_returns_list_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed_texts always returns a list (never raises, never None)."""
        monkeypatch.setattr("seam.analysis.embeddings.is_available", lambda: False)

        from seam.analysis.embeddings import embed_texts

        result = embed_texts([], "BAAI/bge-small-en-v1.5")
        assert isinstance(result, list)

    def test_embed_texts_empty_input_returns_empty(self) -> None:
        """embed_texts([]) returns [] even when is_available=True."""
        with patch("seam.analysis.embeddings.is_available", return_value=True):
            from seam.analysis.embeddings import embed_texts

            result = embed_texts([], "BAAI/bge-small-en-v1.5")
            assert result == []

    def test_embed_texts_never_raises_when_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed_texts must not raise even when called with texts and model unavailable."""
        monkeypatch.setattr("seam.analysis.embeddings.is_available", lambda: False)

        from seam.analysis.embeddings import embed_texts

        try:
            embed_texts(["some text"], "BAAI/bge-small-en-v1.5")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"embed_texts raised unexpectedly: {exc}")

    def test_embed_texts_with_mocked_model_returns_bytes(self) -> None:
        """embed_texts returns list[bytes] when model is mocked to produce float32 blobs.

        Uses struct.pack to create synthetic float32 vectors (no numpy).
        Validates that bytes pass through correctly from _get_model.embed().
        """
        # Synthetic float32 vectors (4 dims each) using struct.pack
        vec1 = _make_float32_bytes([0.1, 0.2, 0.3, 0.4])
        vec2 = _make_float32_bytes([0.5, 0.6, 0.7, 0.8])

        # Mock the model object: embed() returns an iterable of numpy-like arrays.
        # We simulate float32 arrays with a helper that has .tobytes().
        class FakeArray:
            def __init__(self, blob: bytes) -> None:
                self._blob = blob

            def tobytes(self) -> bytes:
                return self._blob

        mock_model = MagicMock()
        mock_model.embed.return_value = iter([FakeArray(vec1), FakeArray(vec2)])

        with patch("seam.analysis.embeddings.is_available", return_value=True):
            with patch("seam.analysis.embeddings._get_model", return_value=mock_model):
                from seam.analysis.embeddings import embed_texts

                result = embed_texts(["text one", "text two"], "BAAI/bge-small-en-v1.5")

        assert isinstance(result, list)
        assert len(result) == 2
        for item in result:
            assert isinstance(item, bytes)

        # Verify bytes round-trip correctly
        recovered1 = _decode_float32_bytes(result[0])
        assert abs(recovered1[0] - 0.1) < 1e-5
        assert abs(recovered1[3] - 0.4) < 1e-5

        recovered2 = _decode_float32_bytes(result[1])
        assert abs(recovered2[0] - 0.5) < 1e-5
        assert abs(recovered2[3] - 0.8) < 1e-5


# ── E4: embed_query degradation ───────────────────────────────────────────────


class TestEmbedQueryDegradation:
    """E4 — embed_query returns empty bytes when fastembed is absent; never raises."""

    def test_embed_query_returns_bytes_when_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed_query returns b'' when fastembed is not available."""
        monkeypatch.setattr("seam.analysis.embeddings.is_available", lambda: False)

        from seam.analysis.embeddings import embed_query

        result = embed_query("retry logic", "BAAI/bge-small-en-v1.5")
        assert isinstance(result, bytes)
        assert result == b""

    def test_embed_query_never_raises_when_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """embed_query must not raise even when fastembed is absent."""
        monkeypatch.setattr("seam.analysis.embeddings.is_available", lambda: False)

        from seam.analysis.embeddings import embed_query

        try:
            embed_query("some concept", "BAAI/bge-small-en-v1.5")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"embed_query raised unexpectedly: {exc}")

    def test_embed_query_with_mocked_model_returns_bytes(self) -> None:
        """embed_query returns bytes for a float32 vector when model is mocked."""
        # Synthetic float32 vector using struct.pack (no numpy)
        query_blob = _make_float32_bytes([0.3, 0.6, 0.9])

        class FakeArray:
            def __init__(self, blob: bytes) -> None:
                self._blob = blob

            def tobytes(self) -> bytes:
                return self._blob

        mock_model = MagicMock()
        mock_model.query_embed.return_value = iter([FakeArray(query_blob)])

        with patch("seam.analysis.embeddings.is_available", return_value=True):
            with patch("seam.analysis.embeddings._get_model", return_value=mock_model):
                from seam.analysis.embeddings import embed_query

                result = embed_query("retry logic", "BAAI/bge-small-en-v1.5")

        assert isinstance(result, bytes)
        assert len(result) == len(query_blob)
        recovered = _decode_float32_bytes(result)
        assert abs(recovered[0] - 0.3) < 1e-5
        assert abs(recovered[2] - 0.9) < 1e-5


# ── E5: Real model tests (skipped in gate — fastembed absent) ─────────────────


class TestRealModelEmbed:
    """E5 — Real-model tests. Skipped unless fastembed is installed."""

    def test_real_embed_texts_returns_correct_dim(self) -> None:
        """Real embed_texts returns 384-dim float32 bytes for bge-small-en-v1.5."""
        pytest.importorskip("fastembed")
        from seam.analysis.embeddings import embed_texts

        results = embed_texts(
            ["hello world", "retry logic with backoff"],
            "BAAI/bge-small-en-v1.5",
        )
        assert len(results) == 2
        for blob in results:
            assert isinstance(blob, bytes)
            # bge-small-en-v1.5 is 384-dim × 4 bytes/float32 = 1536 bytes
            assert len(blob) == 384 * 4

    def test_real_embed_query_returns_correct_dim(self) -> None:
        """Real embed_query returns 384-dim float32 bytes for bge-small-en-v1.5."""
        pytest.importorskip("fastembed")
        from seam.analysis.embeddings import embed_query

        result = embed_query("retry logic", "BAAI/bge-small-en-v1.5")
        assert isinstance(result, bytes)
        assert len(result) == 384 * 4

    def test_real_is_available_true_when_fastembed_installed(self) -> None:
        """is_available() returns True when fastembed IS installed."""
        pytest.importorskip("fastembed")
        from seam.analysis.embeddings import is_available

        assert is_available() is True
