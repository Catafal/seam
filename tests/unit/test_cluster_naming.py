"""Tests for seam/analysis/cluster_naming.py — cluster label generation.

TDD: Tests written before implementation.

Test groups:
    N1 — deterministic_label: expected output from member list
    N2 — label_cluster: naming_source='deterministic' when mode is default
    N3 — label_cluster: LLM enabled (stubbed) → naming_source='llm', returns stub name
    N4 — label_cluster: LLM raises → falls back to deterministic (no network call reached)
    N5 — label_cluster: LLM disabled → no network code path reached at all
"""

from unittest.mock import patch

import pytest

# ── N1: deterministic_label ───────────────────────────────────────────────────


class TestDeterministicLabel:
    """N1: deterministic_label builds a human-readable label from member info."""

    def _make_members(self, names_files: list[tuple[str, str]]) -> list[dict]:
        """Build minimal member dicts (name, file, degree)."""
        return [
            {"name": name, "file": file_path, "degree": i + 1}
            for i, (name, file_path) in enumerate(names_files)
        ]

    def test_label_uses_dominant_directory(self) -> None:
        """When all files share a directory, the label includes that directory."""
        from seam.analysis.cluster_naming import deterministic_label

        members = self._make_members([
            ("parse_python", "seam/indexer/parser.py"),
            ("parse_typescript", "seam/indexer/parser.py"),
            ("_parse", "seam/indexer/parser.py"),
        ])
        label = deterministic_label(members)
        # Label should mention the shared directory or file prefix
        assert "indexer" in label or "parser" in label, f"Expected dir/file in label, got: {label!r}"

    def test_label_includes_highest_degree_symbol(self) -> None:
        """The highest-degree symbol appears in the label."""
        from seam.analysis.cluster_naming import deterministic_label

        members = [
            {"name": "low_degree_fn", "file": "seam/query/engine.py", "degree": 1},
            {"name": "high_degree_fn", "file": "seam/query/engine.py", "degree": 10},
            {"name": "mid_degree_fn", "file": "seam/query/engine.py", "degree": 5},
        ]
        label = deterministic_label(members)
        assert "high_degree_fn" in label, f"Expected highest-degree symbol in label, got: {label!r}"

    def test_label_format_contains_separator(self) -> None:
        """Label should have the 'dir — symbol' format (em dash separator)."""
        from seam.analysis.cluster_naming import deterministic_label

        members = self._make_members([
            ("walk", "seam/analysis/traversal.py"),
            ("context", "seam/analysis/traversal.py"),
        ])
        label = deterministic_label(members)
        assert " — " in label or " - " in label, f"Expected separator in label, got: {label!r}"

    def test_label_single_member(self) -> None:
        """A single-member cluster still produces a non-empty label."""
        from seam.analysis.cluster_naming import deterministic_label

        members = [{"name": "solo_fn", "file": "seam/solo.py", "degree": 1}]
        label = deterministic_label(members)
        assert label, "Single-member cluster must produce non-empty label"
        assert "solo_fn" in label, f"Expected symbol name in single-member label, got: {label!r}"

    def test_label_empty_members_returns_string(self) -> None:
        """Empty member list must not raise; returns a fallback label."""
        from seam.analysis.cluster_naming import deterministic_label

        label = deterministic_label([])
        assert isinstance(label, str)
        # Any non-crashing string is acceptable for the empty case


# ── N2: label_cluster — deterministic mode ────────────────────────────────────


class TestLabelClusterDeterministicMode:
    """N2: label_cluster returns (label, 'deterministic') in default mode."""

    def test_returns_tuple(self) -> None:
        """label_cluster returns a tuple of (str, str)."""
        from seam.analysis.cluster_naming import label_cluster

        members = [{"name": "fn", "file": "seam/mod.py", "degree": 1}]
        result = label_cluster(members, naming_mode="deterministic")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_naming_source_is_deterministic(self) -> None:
        """naming_source must be 'deterministic' in default mode."""
        from seam.analysis.cluster_naming import label_cluster

        members = [{"name": "fn", "file": "seam/mod.py", "degree": 1}]
        _label, source = label_cluster(members, naming_mode="deterministic")
        assert source == "deterministic"

    def test_label_is_non_empty(self) -> None:
        """Deterministic label must be a non-empty string."""
        from seam.analysis.cluster_naming import label_cluster

        members = [
            {"name": "query", "file": "seam/query/engine.py", "degree": 5},
            {"name": "search", "file": "seam/query/engine.py", "degree": 3},
        ]
        label, _ = label_cluster(members, naming_mode="deterministic")
        assert isinstance(label, str)
        assert len(label) > 0


# ── N3: label_cluster — LLM enabled (stubbed) ────────────────────────────────


class TestLabelClusterLLMEnabled:
    """N3: When naming_mode='llm' and key is present, uses stubbed LLM response."""

    def test_llm_naming_uses_llm_response(self) -> None:
        """With LLM enabled and stub returning 'Auth Layer', label='Auth Layer' and source='llm'."""
        from seam.analysis.cluster_naming import label_cluster

        members = [
            {"name": "authenticate", "file": "seam/auth.py", "degree": 5},
            {"name": "validate_token", "file": "seam/auth.py", "degree": 3},
        ]

        # Stub the internal LLM call so no real network request is made
        with patch("seam.analysis.cluster_naming._call_llm_for_label") as mock_llm:
            mock_llm.return_value = "Auth Layer"

            label, source = label_cluster(
                members,
                naming_mode="llm",
                api_key="fake-key",
                model="fake-model",
            )

        assert label == "Auth Layer"
        assert source == "llm"

    def test_llm_naming_passes_member_info_to_llm(self) -> None:
        """The LLM call receives the member list (verifies the interface)."""
        from seam.analysis.cluster_naming import label_cluster

        members = [
            {"name": "authenticate", "file": "seam/auth.py", "degree": 5},
        ]

        with patch("seam.analysis.cluster_naming._call_llm_for_label") as mock_llm:
            mock_llm.return_value = "Auth Layer"
            label_cluster(members, naming_mode="llm", api_key="fake-key", model="gpt")

        # LLM function must have been called with the members
        mock_llm.assert_called_once()
        call_args = mock_llm.call_args
        # First positional arg should be the members list
        assert call_args[0][0] == members


# ── N4: label_cluster — LLM raises → fallback ─────────────────────────────────


class TestLabelClusterLLMFallback:
    """N4: When LLM call raises, falls back to deterministic (fail-safe)."""

    def test_llm_error_falls_back_to_deterministic(self) -> None:
        """Any exception from LLM → deterministic label, source='deterministic'."""
        from seam.analysis.cluster_naming import label_cluster

        members = [
            {"name": "my_fn", "file": "seam/mod.py", "degree": 2},
        ]

        with patch("seam.analysis.cluster_naming._call_llm_for_label") as mock_llm:
            mock_llm.side_effect = RuntimeError("network error")

            label, source = label_cluster(
                members,
                naming_mode="llm",
                api_key="key",
                model="model",
            )

        assert source == "deterministic", "After LLM failure, source must be 'deterministic'"
        assert isinstance(label, str)
        assert len(label) > 0

    def test_llm_timeout_falls_back(self) -> None:
        """Timeout from LLM → deterministic fallback (no crash, no abort)."""

        from seam.analysis.cluster_naming import label_cluster
        members = [{"name": "fn", "file": "seam/mod.py", "degree": 1}]

        with patch("seam.analysis.cluster_naming._call_llm_for_label") as mock_llm:
            mock_llm.side_effect = TimeoutError("request timed out")

            label, source = label_cluster(
                members,
                naming_mode="llm",
                api_key="key",
                model="model",
            )

        assert source == "deterministic"


# ── N5: LLM disabled — no network code reached ────────────────────────────────


class TestLabelClusterLLMDisabled:
    """N5: When naming_mode='deterministic', the LLM path is never invoked."""

    def test_no_llm_call_in_deterministic_mode(self) -> None:
        """With naming_mode='deterministic', _call_llm_for_label is never called."""
        from seam.analysis.cluster_naming import label_cluster

        members = [{"name": "fn", "file": "seam/mod.py", "degree": 1}]

        with patch("seam.analysis.cluster_naming._call_llm_for_label") as mock_llm:
            label_cluster(members, naming_mode="deterministic")

        mock_llm.assert_not_called()

    def test_no_api_key_in_deterministic_mode(self) -> None:
        """No api_key needed for deterministic mode — must not error."""
        from seam.analysis.cluster_naming import label_cluster

        members = [{"name": "fn", "file": "seam/mod.py", "degree": 1}]

        try:
            label, source = label_cluster(members, naming_mode="deterministic")
            assert source == "deterministic"
        except Exception as exc:
            pytest.fail(f"deterministic mode with no api_key raised: {exc}")
