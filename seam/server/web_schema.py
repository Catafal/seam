"""Pydantic response models for diagnostic/source read endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
    http_calls: int
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
    resource_categories: dict[str, int]


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
    has_edge_provenance_column: bool
    has_routes_table: bool
    has_route_nodes: bool
    has_http_calls: bool
    has_config_keys_table: bool
    has_resources_table: bool
    has_config_nodes: bool
    has_resource_nodes: bool
    has_infra_graph: bool
    has_reads_config: bool
    has_configures: bool
    has_exception_edges: bool
    has_test_edges: bool


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
    semantic: dict[str, Any]
    bootstrap: dict[str, Any]
    tools: list[SchemaToolGuide]
    recommended_next_calls: list[str]
    warnings: list[SchemaWarning]
    tables: dict[str, SchemaTableInfo] | None = None


class SearchResultItem(BaseModel):
    """One item in a search result list."""

    uid: str | None = None
    name: str
    kind: str
    file: str
    line: int
    snippet: str | None = None
    score: float | None = None
    signature: str | None
    cluster_id: int | None
    cluster_label: str | None
    retrieval_mode: str | None = None
    retrieval: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    recommended_next_calls: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    """Response for GET /api/search."""

    results: list[SearchResultItem]


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
    provenance: str | None = None
    route_resolved: bool | None = None


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
    recipe: dict[str, Any] | None = None
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
    http_calls: int
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


class ArchitectureExceptionsSection(BaseModel):
    """Exception-flow summary for explicit raises/throws and typed catches."""

    status: str
    raised_types: list[dict[str, Any]] | None = None
    caught_types: list[dict[str, Any]] | None = None
    broad_catches: list[dict[str, Any]] | None = None
    heavy_symbols: list[dict[str, Any]] | None = None
    truncated: int
    reason: str | None = None


class ArchitectureTestsSection(BaseModel):
    """Test/prod split and explicit coverage-edge status."""

    files: dict[str, int]
    coverage_edges: dict[str, Any]
    top_tested_symbols: list[dict[str, Any]] | None = None
    test_heavy_sources: list[dict[str, Any]] | None = None
    untested_hotspots: list[dict[str, Any]] | None = None
    truncated: int | None = None


class ArchitectureOptionalSurface(BaseModel):
    """Status placeholder for config/resource/test-edge surfaces."""

    status: str
    items: list[dict[str, Any]]
    reason: str | None = None


class ArchitectureEvidenceSection(BaseModel):
    """Bounded evidence section with status, count, and optional explanation."""

    status: str
    count: int
    items: list[dict[str, Any]]
    truncated: int
    reason: str | None = None


class ArchitectureSections(BaseModel):
    """Optional architecture sections selected by the caller."""

    summary: ArchitectureSummarySection | None = None
    languages: ArchitectureListSection | None = None
    physical: ArchitecturePhysicalSection | None = None
    clusters: ArchitectureListSection | None = None
    entry_points: ArchitectureListSection | None = None
    routes: ArchitectureListSection | None = None
    http_calls: ArchitectureEvidenceSection | None = None
    configs: ArchitectureListSection | None = None
    resources: ArchitectureListSection | None = None
    infra: ArchitectureListSection | None = None
    hotspots: ArchitectureListSection | None = None
    orchestrators: ArchitectureListSection | None = None
    boundaries: ArchitectureListSection | None = None
    edge_mix: ArchitectureEdgeMixSection | None = None
    exceptions: ArchitectureExceptionsSection | None = None
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


class StatusResponse(BaseModel):
    """Response for GET /api/status.

    stale + stale_reason are ADDITIVE fields added in #272.
    They mirror the same check_staleness() call used by the MCP graph-traversal
    handlers so /api/status can never disagree with `seam status`.
    stale_reason is None when the index is fresh.
    """

    root: str
    symbol_count: int
    edge_count: int
    cluster_count: int
    last_indexed: str | None
    languages: list[str]
    # Additive staleness fields (#272): watcher-aware, derived from staleness.py.
    stale: bool
    stale_reason: str | None
    semantic: dict[str, Any] | None = None


class StructureSymbol(BaseModel):
    """One symbol row for the structure map (flat — the SPA builds the tree).

    B2: `degree` = fan-in / incoming edge count (edges pointing TO this symbol).
    Computed at read time via an additive LEFT JOIN — no schema change, no re-index.
    Zero when a symbol has no incoming edges (isolated or outgoing-only).
    """

    path: str
    name: str
    kind: str
    line: int
    qualified_name: str | None
    degree: int


class StructureResponse(BaseModel):
    """Response for GET /api/structure."""

    symbols: list[StructureSymbol]
