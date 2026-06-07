"""Edge-synthesis engine — pure function, no DB access, deterministic, never raises.

LAYER: leaf — imports only stdlib. No seam deps (same leaf pattern as clustering.py).

This module implements whole-graph edge synthesis: given the already-extracted symbols,
edges, and per-file source text, it synthesizes additional edges that a parser cannot see
(dynamic dispatch, interface dispatch, observer callbacks, etc.) and returns them as a list
of edge-like dicts ready for persistence.

Channels implemented in THIS module (Slice #1):
  A2 — interface-override: for each concrete class C that has an extends/implements
       edge to a base/interface B, link every method of B to every same-name method
       of C as a synthesized 'call' edge. This is a deliberate OVER-APPROXIMATION —
       all same-name implementations are linked; no MRO, no transitive base walk.
       Direct subtypes only (one hop). Bounded by fanout_cap.

Future channels (A1 closure-collection, EventEmitter/observer) will be added to this
module and called from synthesize_edges() without changing the public signature.

Design rules (shared with clustering.py):
  - Public function synthesize_edges() is PURE: no side effects, deterministic output.
  - Never raises: all internal errors degrade to "no edge emitted" (same contract as
    parsers: conservatism over explosions). The caller (synthesis_index.py) handles errors.
  - Cap-bounded: fanout_cap limits edges per base-method to prevent graph explosions.
  - Pairing by string names only — no node IDs, no graph DB. Mirrors how the rest of
    Seam stores edges: source/target are plain symbol name strings.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Channel identifier for the interface-override synthesis.
# Stored verbatim in edges.synthesized_by for provenance tracing.
_CHANNEL_INTERFACE_OVERRIDE = "interface-override"

# Inheritance edge kinds that trigger A2 override fan-out.
_INHERITANCE_KINDS = frozenset({"extends", "implements"})


def synthesize_edges(
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    file_sources: dict[str, str],
    fanout_cap: int,
) -> list[dict[str, Any]]:
    """Synthesize dynamic-dispatch edges from the whole-graph symbol and edge data.

    This is the main entry point for the synthesis engine. It is PURE: it reads
    from the supplied in-memory data structures and returns a list of new edge dicts,
    never touching the database or raising any exception.

    Args:
        symbols:      List of symbol dicts from the DB (must have 'name' and 'kind' keys).
        edges:        List of edge dicts from the DB (must have 'source', 'target', 'kind').
        file_sources: Dict mapping file path → source text (reserved for A1 channels;
                      the A2 channel does not use source text but accepts it for a stable
                      API so slice 2 can add channels without changing this signature).
        fanout_cap:   Maximum synthesized edges per base-method across all implementations.
                      0 = unlimited (no cap applied).

    Returns:
        List of edge-like dicts, each with keys:
          source, target, kind, confidence, synthesized_by
        (and sentinel values for file/line since synthesized edges are not file-scoped).

    Never raises: any internal error degrades to returning whatever edges were built
    before the error occurred, and logs a warning.
    """
    try:
        return _run_a2_interface_override(symbols, edges, fanout_cap)
    except Exception as exc:  # noqa: BLE001
        # Never raise — degrade to empty list and log the error.
        logger.warning(
            "synthesis: A2 interface-override channel failed (%s: %s) — "
            "returning partial results (no synthesized edges from this channel)",
            type(exc).__name__,
            exc,
        )
        return []


def _run_a2_interface_override(
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    fanout_cap: int,
) -> list[dict[str, Any]]:
    """A2 channel: interface/base→implementation method fan-out.

    Algorithm (one hop, no transitive walk, deliberate over-approximation):

    1. Build a set of all symbol names that are methods (kind='method').
       Symbol names for methods follow the 'ClassName.methodName' qualified pattern.

    2. For each symbol with an 'extends' or 'implements' edge pointing to a base B,
       the source of that edge is the concrete class C (C extends/implements B).

    3. For each method B.m of base B that exists in the symbol table,
       check if C.m also exists. If so, emit:
         source=B.m, target=C.m, kind='call', confidence='INFERRED',
         synthesized_by='interface-override'

    4. Apply fanout_cap: if fanout_cap > 0, cap at most fanout_cap implementations
       per base method (i.e. at most fanout_cap edges per source='B.m').

    WHY over-approximation: correctly resolving which implementation runs at a given
    call site would require type inference, MRO, or runtime analysis. Seam's synthesis
    contract is: emit ALL plausible dispatch targets (conservative on false negatives),
    with a cap to prevent graph explosions. An agent querying seam_impact sees ALL
    implementations — which is the right default for blast-radius analysis.

    WHY one-hop only: if C extends B extends A, this channel emits B.m→C.m and A.m→B.m
    separately (via their respective direct inheritance edges) — both are emitted in the
    same pass because both are direct inheritance edges in the edges table. No transitive
    walk is required to achieve this; the direct-edge scan naturally covers all levels.
    """
    # ── Step 1: Build symbol lookup structures ────────────────────────────────
    # method_names: set of all qualified method names 'ClassName.methodName'.
    # class_methods: maps class/interface name → set of bare method names.
    method_names: set[str] = set()
    class_methods: dict[str, set[str]] = {}

    for sym in symbols:
        try:
            name = sym.get("name", "")
            kind = sym.get("kind", "")
            if not name or not kind:
                continue
            if kind == "method" and "." in name:
                method_names.add(name)
                cls, bare = name.split(".", 1)
                class_methods.setdefault(cls, set()).add(bare)
        except Exception:  # noqa: BLE001
            # Malformed symbol dict — skip silently (never-raise contract).
            continue

    # ── Step 2: Build inheritance map: subclass → set of direct bases ─────────
    # sub_to_bases: maps 'ConcreteClass' → {'IBase1', 'IBase2', ...}
    sub_to_bases: dict[str, set[str]] = {}

    for edge in edges:
        try:
            kind = edge.get("kind", "")
            if kind not in _INHERITANCE_KINDS:
                continue
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if not src or not tgt:
                continue
            sub_to_bases.setdefault(src, set()).add(tgt)
        except Exception:  # noqa: BLE001
            continue

    # ── Step 3: Synthesize override call edges ────────────────────────────────
    # For each (concrete_class, base) pair from inheritance edges,
    # for each method of base, check if concrete_class also has that method.
    # Track per-base-method emission count to enforce fanout_cap.

    # base_method_count: base_method_name → number of synth edges emitted so far
    base_method_count: dict[str, int] = {}
    result: list[dict[str, Any]] = []

    # Sort for determinism (same graph → same edges, same order).
    for sub_cls in sorted(sub_to_bases):
        bases = sorted(sub_to_bases[sub_cls])
        for base in bases:
            base_bare_methods = class_methods.get(base, set())
            if not base_bare_methods:
                continue  # Base has no indexed methods — skip.

            sub_bare_methods = class_methods.get(sub_cls, set())
            if not sub_bare_methods:
                continue  # Concrete class has no methods — skip.

            # Emit an edge for each method name shared between base and subclass.
            for bare_method in sorted(base_bare_methods):
                if bare_method not in sub_bare_methods:
                    continue  # No matching implementation in subclass — skip.

                base_method_name = f"{base}.{bare_method}"
                impl_method_name = f"{sub_cls}.{bare_method}"

                # Apply fanout_cap per base method.
                if fanout_cap > 0:
                    current_count = base_method_count.get(base_method_name, 0)
                    if current_count >= fanout_cap:
                        logger.debug(
                            "synthesis: A2 fanout_cap=%d reached for %s — skipping %s",
                            fanout_cap,
                            base_method_name,
                            impl_method_name,
                        )
                        continue
                    base_method_count[base_method_name] = current_count + 1

                result.append({
                    "source": base_method_name,
                    "target": impl_method_name,
                    "kind": "call",
                    "confidence": "INFERRED",
                    "synthesized_by": _CHANNEL_INTERFACE_OVERRIDE,
                    # Synthesized edges are not file-scoped; use sentinel values.
                    # index_synthesis stores these under a special synthetic file row.
                    "file": ":synthesis:",
                    "line": 0,
                })

    logger.debug(
        "synthesis: A2 interface-override channel emitted %d edges "
        "(%d subclass→base pairs, %d base methods checked)",
        len(result),
        sum(len(v) for v in sub_to_bases.values()),
        len(base_method_count),
    )
    return result
