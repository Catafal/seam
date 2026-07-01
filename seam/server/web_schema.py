"""Pydantic response models for the Web schema diagnostics endpoint."""

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
