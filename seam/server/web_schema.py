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
    routes: int
    config_keys: int
    resources: int


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
    has_routes_table: bool
    has_route_nodes: bool
    has_http_calls: bool
    has_config_keys_table: bool
    has_resources_table: bool
    has_config_nodes: bool
    has_resource_nodes: bool
    has_reads_config: bool
    has_configures: bool


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


class GraphSearchDegreeSummary(BaseModel):
    """Incoming/outgoing/total relationship counts for a structural result."""

    incoming: int
    outgoing: int
    total: int


class GraphSearchPreviewItem(BaseModel):
    """One bounded connected-node preview entry."""

    direction: str
    symbol: str
    uid: str | None
    kind: str | None
    file: str
    line: int
    edge_kind: str
    confidence: str
    receiver: str | None = None
    synthesized_by: str | None = None


class GraphSearchItem(BaseModel):
    """One structural graph-search hit, intentionally metadata-only."""

    symbol: str
    uid: str
    kind: str
    file: str
    line: int
    end_line: int
    signature: str | None
    qualified_name: str | None
    visibility: str | None
    is_exported: bool | None
    language: str | None
    cluster_id: int | None
    cluster_label: str | None
    is_test: bool
    degrees: GraphSearchDegreeSummary
    preview: list[GraphSearchPreviewItem] | None = None
    preview_truncated: bool | None = None


class GraphSearchResponse(BaseModel):
    """Response for GET /api/graph/search."""

    query: dict[str, Any]
    items: list[GraphSearchItem]
    total: int
    limit: int
    offset: int
    has_more: bool
    warnings: list[SchemaWarning]


class ArchitectureIdentity(BaseModel):
    """Version identity for GET /api/architecture."""

    schema_version: int | str
    seam_version: str
    index_seam_version: str | None


class ArchitectureFreshness(BaseModel):
    """Index freshness block in GET /api/architecture."""

    stale: bool
    reason: str | None
    hint: str | None


class ArchitectureScope(BaseModel):
    """Scope application metadata for GET /api/architecture."""

    path: str | None
    applied: bool


class ArchitectureCounts(BaseModel):
    """Scoped and global population counts for GET /api/architecture."""

    files: int
    symbols: int
    edges: int
    clusters: int
    comments: int
    import_mappings: int
    embeddings: int
    routes: int
    config_keys: int
    resources: int
    test_files: int
    production_files: int
    unknown_files: int


class ArchitectureSummarySection(BaseModel):
    """Human-readable one-line summary."""

    text: str


class ArchitectureListSection(BaseModel):
    """Ranked section with bounded, schema-flexible item dictionaries."""

    items: list[dict[str, Any]]
    truncated: int


class ArchitecturePhysicalSection(BaseModel):
    """Filesystem-oriented architecture section."""

    top_areas: list[dict[str, Any]]
    structure: dict[str, Any]
    truncated: int


class ArchitectureEdgeMixSection(BaseModel):
    """Relationship-kind and confidence distribution."""

    edge_kinds: dict[str, int]
    confidence: dict[str, int]
    synthesized: dict[str, int]
    synthesized_total: int


class ArchitectureTestsSection(BaseModel):
    """Test/prod split and explicit coverage-edge status."""

    files: dict[str, int]
    coverage_edges: dict[str, Any]


class ArchitectureOptionalSurface(BaseModel):
    """Status placeholder for config/resource/test-edge surfaces."""

    status: str
    items: list[dict[str, Any]]
    reason: str | None = None


class ArchitectureSections(BaseModel):
    """Optional architecture sections selected by the caller."""

    summary: ArchitectureSummarySection | None = None
    languages: ArchitectureListSection | None = None
    physical: ArchitecturePhysicalSection | None = None
    clusters: ArchitectureListSection | None = None
    entry_points: ArchitectureListSection | None = None
    routes: ArchitectureListSection | None = None
    configs: ArchitectureListSection | None = None
    resources: ArchitectureListSection | None = None
    hotspots: ArchitectureListSection | None = None
    orchestrators: ArchitectureListSection | None = None
    boundaries: ArchitectureListSection | None = None
    edge_mix: ArchitectureEdgeMixSection | None = None
    tests: ArchitectureTestsSection | None = None
    optional_surfaces: dict[str, ArchitectureOptionalSurface] | None = None


class ArchitectureNextCall(BaseModel):
    """Recommended follow-up Seam call."""

    tool: str
    reason: str
    params: dict[str, Any]


class ArchitectureResponse(BaseModel):
    """Response for GET /api/architecture."""

    identity: ArchitectureIdentity
    freshness: ArchitectureFreshness
    scope: ArchitectureScope
    counts: ArchitectureCounts
    sections: ArchitectureSections
    warnings: list[SchemaWarning]
    truncation: dict[str, Any]
    next_calls: list[ArchitectureNextCall]
