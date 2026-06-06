"""Tier D #12 — identifier compound-split tokenization (search recall on camelCase).

split_identifier turns a code identifier into its lowercased sub-word tokens so FTS5
(whose default unicode61 tokenizer treats `GlobalPushToTalkShortcutMonitor` as ONE opaque
token) can match a natural-language query like "push to talk monitor". build_search_text
unions the splits of name + qualified_name (deduped) — the text stored in symbols.search_text.

These are pure leaf functions: never raise, deterministic, no DB.
"""

from seam.indexer.tokenize import build_search_text, split_identifier


class TestSplitIdentifier:
    def test_camel_case(self) -> None:
        assert split_identifier("GlobalPushToTalkShortcutMonitor") == [
            "global", "push", "to", "talk", "shortcut", "monitor",
        ]

    def test_lower_camel(self) -> None:
        assert split_identifier("captureAllScreensAsJPEG") == [
            "capture", "all", "screens", "as", "jpeg",
        ]

    def test_acronym_then_word(self) -> None:
        # leading ALLCAPS acronym followed by a Word splits at the acronym boundary
        assert split_identifier("HTTPServer") == ["http", "server"]
        assert split_identifier("parseJSONData") == ["parse", "json", "data"]

    def test_snake_case(self) -> None:
        assert split_identifier("find_cycle") == ["find", "cycle"]

    def test_dotted_qualified(self) -> None:
        assert split_identifier("CompanionManager.parsePointingCoordinates") == [
            "companion", "manager", "parse", "pointing", "coordinates",
        ]

    def test_digit_boundary(self) -> None:
        # a trailing digit stays attached to its word; the next Capitalized word splits off
        assert split_identifier("v2Loader") == ["v2", "loader"]

    def test_mixed_separators(self) -> None:
        assert split_identifier("leanring_buddyApp") == ["leanring", "buddy", "app"]

    def test_single_word_lowercased(self) -> None:
        assert split_identifier("orchestrate") == ["orchestrate"]

    def test_dedupe_preserves_order(self) -> None:
        # repeated sub-word appears once, first-seen order preserved
        assert split_identifier("UserUserService") == ["user", "service"]

    def test_empty_and_garbage_never_raise(self) -> None:
        assert split_identifier("") == []
        assert split_identifier("___") == []
        assert split_identifier("...") == []

    def test_returns_list(self) -> None:
        assert isinstance(split_identifier("FooBar"), list)


class TestBuildSearchText:
    def test_name_only(self) -> None:
        assert build_search_text("GlobalPushToTalkShortcutMonitor") == (
            "global push to talk shortcut monitor"
        )

    def test_folds_qualified_name_deduped(self) -> None:
        # qualified_name contributes the namespace word 'pipeline' not present in the bare name
        out = build_search_text("run_pipeline", "pipeline.runner.run_pipeline")
        toks = out.split()
        assert "run" in toks and "pipeline" in toks and "runner" in toks
        # 'run' and 'pipeline' from the name are not duplicated by the qualified fold
        assert toks.count("run") == 1
        assert toks.count("pipeline") == 1

    def test_none_qualified_name(self) -> None:
        assert build_search_text("find_cycle", None) == "find cycle"

    def test_empty_inputs(self) -> None:
        assert build_search_text("", None) == ""
