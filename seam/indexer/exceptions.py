"""Conservative exception-flow extraction for explicit raise/throw/catch evidence.

Owns: turning visible exception syntax into normal Seam graph edges.
Does not own: runtime exception propagation, thrown-variable data flow, or broad
language-specific framework semantics.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from seam.indexer.graph_common import Edge, Symbol, _find_enclosing_function, _text


def _resolve_confidence(target_name: str, symbols: list[Symbol]) -> str:
    final_name = target_name.rsplit(".", 1)[-1]
    count = 0
    for symbol in symbols:
        name = symbol["name"]
        if name == target_name or name == final_name or name.rsplit(".", 1)[-1] == final_name:
            count += 1
    if count == 1:
        return "EXTRACTED"
    if count > 1:
        return "AMBIGUOUS"
    return "INFERRED"


def _walk(node: Node) -> list[Node]:
    out = [node]
    for child in node.children:
        out.extend(_walk(child))
    return out


def _python_exception_name(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "identifier":
        return _text(node)
    if node.type == "as_pattern":
        return _python_exception_name(next(iter(node.named_children), None))
    if node.type == "call":
        return _python_exception_name(node.child_by_field_name("function"))
    if node.type == "attribute":
        return _text(node)
    if node.type == "dotted_name":
        return _text(node)
    return None


def _python_except_targets(except_clause: Node) -> list[str]:
    candidates: list[Node] = []
    for child in except_clause.named_children:
        if child.type == "block":
            break
        candidates.append(child)
    if not candidates:
        return []

    def collect(node: Node) -> list[str]:
        if node.type == "tuple":
            names: list[str] = []
            for child in node.named_children:
                name = _python_exception_name(child)
                if name:
                    names.append(name)
            return names
        name = _python_exception_name(node)
        return [name] if name else []

    targets: list[str] = []
    for candidate in candidates:
        targets.extend(collect(candidate))
    return targets


def _ts_exception_name(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "new_expression":
        for child in node.named_children:
            if child.type in {"identifier", "type_identifier", "member_expression"}:
                name = _text(child)
                final_name = name.rsplit(".", 1)[-1]
                return name if final_name == "Error" or final_name.endswith("Error") else None
    if node.type == "call_expression":
        callee = node.child_by_field_name("function")
        if callee is not None and callee.type == "identifier":
            name = _text(callee)
            # Ordinary calls can throw internally; only Error constructors are
            # explicit exception evidence.
            return name if name == "Error" or name.endswith("Error") else None
    return None


def extract_exception_edges(
    root: Node,
    language: str,
    filepath: Path,
    symbols: list[Symbol],
) -> list[Edge]:
    """Extract explicit exception edges from one parsed source file.

    The extractor intentionally emits only local syntactic evidence because
    guessing propagation through callees would make the graph look more precise
    than static indexing can prove.
    """
    try:
        if language == "python":
            return _extract_python_exception_edges(root, filepath, symbols)
        if language in {"typescript", "javascript"}:
            return _extract_ts_exception_edges(root, language, filepath, symbols)
    except Exception:  # noqa: BLE001 - one bad AST shape must not abort indexing
        return []
    return []


def _edge(
    *,
    source: str,
    target: str,
    kind: str,
    filepath: Path,
    line: int,
    symbols: list[Symbol],
) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind=kind,
        file=str(filepath),
        line=line,
        confidence=_resolve_confidence(target, symbols),  # type: ignore[typeddict-item]
        receiver=None,
    )


def _extract_python_exception_edges(root: Node, filepath: Path, symbols: list[Symbol]) -> list[Edge]:
    edges: list[Edge] = []
    for node in _walk(root):
        if node.type == "raise_statement":
            source = _find_enclosing_function(node, "python")
            if source is None:
                continue
            expression = next(iter(node.named_children), None)
            target = _python_exception_name(expression)
            if target is None:
                continue
            edges.append(_edge(
                source=source,
                target=target,
                kind="raises",
                filepath=filepath,
                line=node.start_point[0] + 1,
                symbols=symbols,
            ))
        elif node.type == "except_clause":
            source = _find_enclosing_function(node, "python")
            if source is None:
                continue
            for target in _python_except_targets(node):
                edges.append(_edge(
                    source=source,
                    target=target,
                    kind="catches",
                    filepath=filepath,
                    line=node.start_point[0] + 1,
                    symbols=symbols,
                ))
    return edges


def _extract_ts_exception_edges(
    root: Node,
    language: str,
    filepath: Path,
    symbols: list[Symbol],
) -> list[Edge]:
    edges: list[Edge] = []
    for node in _walk(root):
        if node.type != "throw_statement":
            continue
        source = _find_enclosing_function(node, language)
        if source is None:
            continue
        expression = next(iter(node.named_children), None)
        target = _ts_exception_name(expression)
        if target is None:
            continue
        edges.append(_edge(
            source=source,
            target=target,
            kind="raises",
            filepath=filepath,
            line=node.start_point[0] + 1,
            symbols=symbols,
        ))
    return edges
