"""Pydantic response models for diagnostic/source read endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SchemaFreshness(BaseModel):
    """Index freshness block in GET /api/schema."""

    stale: bool
    reason: str | None
    hint: str | None


class SchemaCounts(BaseModel):
    """Top-level index population counts in GET /api/schema."""

    files: int
    symbols: int
    edges: int
    clusters: int
    comments: int
    import_mappings: int
    embeddings: int


class SchemaBreakdowns(BaseModel):
    """Small grouped-count maps for GET /api/schema."""

    languages: dict[str, int]
    symbol_kinds: dict[str, int]
    edge_kinds: dict[str, int]
    edge_confidence: dict[str, int]
    synthesized_edges: dict[str, int]
    comment_markers: dict[str, int]
    embedding_models: dict[str, int]


class SchemaCapabilities(BaseModel):
    """Derived feature booleans for GET /api/schema."""

    has_clusters: bool
    has_comments: bool
    has_import_mappings: bool
    has_embeddings: bool
    embedding_model_matches: bool
    has_synthesized_edges: bool
    has_field_symbols: bool
    has_receiver_column: bool
    has_search_text: bool
    has_signature_column: bool
    has_synthesized_by_column: bool


class SchemaToolGuide(BaseModel):
    """One tool-guidance entry in GET /api/schema."""

    name: str
    transports: list[str]
    read_only: bool
    use_when: str
    depends_on: list[str] | None = None


class SchemaWarning(BaseModel):
    """Structured warning emitted by GET /api/schema."""

    code: str
    message: str
    hint: str


class SchemaColumnInfo(BaseModel):
    """Verbose column metadata for GET /api/schema?verbose=true."""

    exists: bool
    type: str | None
    notnull: bool
    default: Any
    primary_key: bool


class SchemaTableInfo(BaseModel):
    """Verbose table metadata for GET /api/schema?verbose=true."""

    exists: bool
    columns: dict[str, SchemaColumnInfo]


class SchemaResponse(BaseModel):
    """Response for GET /api/schema."""

    schema_version: int | str
    seam_version: str
    index_seam_version: str | None
    freshness: SchemaFreshness
    counts: SchemaCounts
    breakdowns: SchemaBreakdowns
    capabilities: SchemaCapabilities
    tools: list[SchemaToolGuide]
    recommended_next_calls: list[str]
    warnings: list[SchemaWarning]
    tables: dict[str, SchemaTableInfo] | None = None


class SnippetWarning(BaseModel):
    """Machine-readable warning so UI callers can route stale/truncated reads distinctly."""

    code: str
    message: str
    hint: str


class SnippetCandidate(BaseModel):
    """Candidate selector returned instead of guessing when a source lookup is ambiguous."""

    symbol: str
    uid: str
    kind: str
    file: str
    start_line: int
    end_line: int
    signature: str | None


class SnippetTruncation(BaseModel):
    """Truncation metadata preserves the difference between a small symbol and a capped read."""

    by_lines: bool
    by_bytes: bool
    original_line_count: int
    returned_line_count: int


class SnippetFreshness(BaseModel):
    """Freshness signals protect callers from trusting line ranges after local edits."""

    file_hash_matches: bool
    mtime_matches: bool
    index_stale: bool


class SnippetResponse(BaseModel):
    """Typed union-style payload because not-found and ambiguity are successful reads."""

    found: bool
    symbol: str | None = None
    uid: str | None = None
    kind: str | None = None
    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    signature: str | None = None
    docstring: str | None = None
    source: str | None = None
    truncated: SnippetTruncation | None = None
    freshness: SnippetFreshness | None = None
    neighbors: list[SnippetCandidate] | None = None
    ambiguous: bool | None = None
    reason: str | None = None
    message: str | None = None
    candidates: list[SnippetCandidate] = []
    warnings: list[SnippetWarning]
