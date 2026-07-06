"""Lightweight document-anchor extraction and grounding resolution.

The document grounding surface is intentionally conservative: it records
explicit local evidence from Markdown, then resolves only exact references
against the existing index. It does not infer semantic agreement between docs
and code, and it never creates dependency edges.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

import seam.config as config

DocConfidence = Literal["EXACT", "HIGH", "MEDIUM", "LOW"]


class DocumentAnchor(TypedDict):
    heading_path: str
    slug: str
    anchor_type: str
    start_line: int
    end_line: int
    search_text: str


class DocumentReference(TypedDict):
    heading_path: str
    target_kind: str
    target_value: str
    resolved_kind: str | None
    resolved_value: str | None
    relation_type: str
    confidence: DocConfidence
    line: int
    provenance: str
    caveat: str | None


class DocumentIndex(TypedDict):
    path: Path
    doc_kind: str
    status: str
    title: str | None
    anchors: list[DocumentAnchor]
    references: list[DocumentReference]
    warnings: list[dict[str, str]]


@dataclass(frozen=True)
class _RawRef:
    heading_path: str
    target_kind: str
    target_value: str
    relation_type: str
    confidence: DocConfidence
    line: int
    provenance: str
    caveat: str | None = None


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FRONTMATTER_STATUS_RE = re.compile(r"^status\s*:\s*[\"']?([^\"'\n]+)", re.IGNORECASE)
_VISIBLE_STATUS_RE = re.compile(r"\bstatus\s*:\s*`?([^`.\n]+)", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CODE_SPAN_RE = re.compile(r"`([^`\n]{1,180})`")
_ISSUE_RE = re.compile(r"(?<![A-Za-z0-9_])#(\d{1,7})(?![A-Za-z0-9_])")
_ROUTE_RE = re.compile(r"^/[A-Za-z0-9_./{}:$*-]+$")
_CONFIG_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_SYMBOLISH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SECRET_LINE_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|private[_-]?key)\b\s*[:=]"
)
_DOC_SUFFIXES = {".md", ".markdown"}
_CODE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".cs",
    ".rb", ".c", ".cc", ".cpp", ".h", ".hpp", ".php", ".swift",
}


def is_document_file(path: Path) -> bool:
    """Return True when `path` is a local documentation file for grounding."""
    return path.suffix.lower() in _DOC_SUFFIXES


def classify_doc(path: Path, text: str) -> tuple[str, str, str | None]:
    """Classify document kind, status, and title using local conventions only."""
    rel = str(path).lower()
    name = path.name.lower()
    title = _first_title(text)
    if "/adr/" in rel or name.startswith("adr-") or re.match(r"\d{3,}[-_]", name):
        kind = "adr"
    elif "/prd/" in rel or name.startswith("prd") or "prd" in name:
        kind = "prd"
    elif "roadmap" in name or "roadmap" in rel:
        kind = "roadmap"
    elif "/.claude/tasks/" in rel or "/tasks/" in rel:
        kind = "task"
    elif "benchmark" in name or "/eval/" in rel:
        kind = "benchmark"
    elif name == "readme.md":
        kind = "readme"
    elif "architecture" in name:
        kind = "architecture"
    elif "concept" in name:
        kind = "concept"
    elif "contract" in name:
        kind = "api-contract"
    else:
        kind = "guide"
    return kind, _extract_status(text), title


def extract_document(path: Path, root: Path, text: str) -> tuple[DocumentIndex, list[_RawRef]]:
    """Extract Markdown anchors and raw explicit references from a document."""
    doc_kind, status, title = classify_doc(path, text)
    lines = text.splitlines()
    heading_stack: list[tuple[int, str]] = []
    anchors: list[DocumentAnchor] = []
    raw_refs: list[_RawRef] = []
    warnings: list[dict[str, str]] = []
    starts: list[tuple[int, str, str]] = []

    if title is None:
        starts.append((1, "Document", "document"))
    for idx, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        heading = _clean_heading(match.group(2))
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, heading))
        starts.append((idx, " > ".join(item[1] for item in heading_stack), "heading"))

    if not starts:
        starts.append((1, title or path.name, "document"))

    for pos, (start, heading_path, anchor_type) in enumerate(starts):
        end = (starts[pos + 1][0] - 1) if pos + 1 < len(starts) else max(1, len(lines))
        chunk = lines[start - 1 : end]
        search_text = _bounded_search_text(chunk, heading_path)
        anchors.append(
            {
                "heading_path": heading_path,
                "slug": _slugify(heading_path),
                "anchor_type": anchor_type,
                "start_line": start,
                "end_line": end,
                "search_text": search_text,
            }
        )
        raw_refs.extend(_references_from_chunk(chunk, start, heading_path, path, root))

    if any(_SECRET_LINE_RE.search(line) for line in lines):
        warnings.append(
            {
                "code": "SECRET_LIKE_TEXT_OMITTED",
                "message": "Secret-looking assignment lines were excluded from anchor search text.",
            }
        )

    return (
        {
            "path": path,
            "doc_kind": doc_kind,
            "status": status,
            "title": title,
            "anchors": anchors,
            "references": [],
            "warnings": warnings,
        },
        raw_refs,
    )


def resolve_document_references(
    conn: sqlite3.Connection,
    root: Path,
    raw_refs: list[_RawRef],
) -> list[DocumentReference]:
    """Resolve raw references against indexed symbols/files/routes/config/resources."""
    resolved: list[DocumentReference] = []
    for ref in raw_refs:
        resolved_kind, resolved_value, confidence, caveat = _resolve_ref(conn, root, ref)
        resolved.append(
            {
                "heading_path": ref.heading_path,
                "target_kind": ref.target_kind,
                "target_value": ref.target_value,
                "resolved_kind": resolved_kind,
                "resolved_value": resolved_value,
                "relation_type": ref.relation_type,
                "confidence": confidence,
                "line": ref.line,
                "provenance": ref.provenance,
                "caveat": caveat or ref.caveat,
            }
        )
    return resolved


def extract_and_resolve_document(
    conn: sqlite3.Connection,
    root: Path,
    path: Path,
) -> DocumentIndex:
    """Extract and resolve one document file. Never performs network I/O."""
    text = path.read_text(encoding="utf-8", errors="replace")
    doc, raw_refs = extract_document(path, root, text)
    doc["references"] = resolve_document_references(conn, root, raw_refs)
    return doc


def _first_title(text: str) -> str | None:
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            return _clean_heading(match.group(2))
    return None


def _extract_status(text: str) -> str:
    in_frontmatter = False
    for idx, line in enumerate(text.splitlines()[:40]):
        stripped = line.strip()
        if idx == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter and stripped == "---":
            in_frontmatter = False
            continue
        match = _FRONTMATTER_STATUS_RE.match(stripped)
        if match:
            return _normalize_status(match.group(1))
        match = _VISIBLE_STATUS_RE.search(stripped)
        if match:
            return _normalize_status(match.group(1))
    return "unknown"


def _normalize_status(value: str) -> str:
    clean = value.strip().strip(".").lower().replace(" ", "-")
    for sep in (";", ",", "·", "|"):
        clean = clean.split(sep, 1)[0].strip()
    aliases = {
        "ready-for-implementation": "ready-for-agent",
        "ready": "ready-for-agent",
        "done": "shipped",
        "complete": "shipped",
        "completed": "shipped",
    }
    return aliases.get(clean, clean or "unknown")


def _clean_heading(value: str) -> str:
    value = re.sub(r"\s+#+$", "", value).strip()
    return re.sub(r"<[^>]+>", "", value).strip()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "document"


def _bounded_search_text(lines: list[str], heading_path: str) -> str:
    kept: list[str] = [heading_path]
    for line in lines[: config.SEAM_GROUNDING_MAX_ANCHOR_LINES]:
        if _SECRET_LINE_RE.search(line):
            continue
        compact = line.strip()
        if compact:
            kept.append(compact)
        joined = "\n".join(kept)
        if len(joined) >= config.SEAM_GROUNDING_MAX_ANCHOR_CHARS:
            return joined[: config.SEAM_GROUNDING_MAX_ANCHOR_CHARS]
    return "\n".join(kept)[: config.SEAM_GROUNDING_MAX_ANCHOR_CHARS]


def _references_from_chunk(
    lines: list[str],
    start_line: int,
    heading_path: str,
    doc_path: Path,
    root: Path,
) -> list[_RawRef]:
    refs: list[_RawRef] = []
    seen: set[tuple[str, str, int, str]] = set()
    for offset, line in enumerate(lines):
        line_no = start_line + offset
        for label, target in _MD_LINK_RE.findall(line):
            ref = _ref_from_markdown_link(label, target, line_no, heading_path, doc_path, root)
            if ref is not None:
                _append_unique(refs, seen, ref)
        for token in _CODE_SPAN_RE.findall(line):
            ref = _ref_from_code_span(token.strip(), line_no, heading_path)
            if ref is not None:
                _append_unique(refs, seen, ref)
        for issue in _ISSUE_RE.findall(line):
            _append_unique(
                refs,
                seen,
                _RawRef(
                    heading_path,
                    "issue",
                    f"#{issue}",
                    "references_issue",
                    "EXACT",
                    line_no,
                    "issue-shorthand",
                ),
            )
    return refs


def _append_unique(
    refs: list[_RawRef],
    seen: set[tuple[str, str, int, str]],
    ref: _RawRef,
) -> None:
    key = (ref.target_kind, ref.target_value, ref.line, ref.provenance)
    if key not in seen:
        refs.append(ref)
        seen.add(key)


def _ref_from_markdown_link(
    label: str,
    target: str,
    line: int,
    heading_path: str,
    doc_path: Path,
    root: Path,
) -> _RawRef | None:
    target = target.split("#", 1)[0].strip()
    if not target or re.match(r"^[a-z]+://", target) or target.startswith("mailto:"):
        return None
    if target.startswith("/"):
        candidate = root / target.lstrip("/")
    else:
        candidate = (doc_path.parent / target).resolve()
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        return None
    value = rel.as_posix()
    if candidate.suffix.lower() in _DOC_SUFFIXES:
        relation = "supersedes_doc" if "supersede" in label.lower() else "mentions_doc"
        return _RawRef(heading_path, "doc", value, relation, "EXACT", line, "markdown-link")
    if candidate.suffix.lower() in _CODE_SUFFIXES or candidate.exists():
        return _RawRef(heading_path, "file", value, "mentions_file", "EXACT", line, "markdown-link")
    return None


def _ref_from_code_span(token: str, line: int, heading_path: str) -> _RawRef | None:
    if not token or len(token) > 160 or token.startswith(("http://", "https://")):
        return None
    if _ROUTE_RE.match(token):
        return _RawRef(heading_path, "route", token, "mentions_route", "HIGH", line, "code-span")
    if _CONFIG_RE.match(token):
        return _RawRef(heading_path, "config", token, "mentions_config", "HIGH", line, "code-span")
    if "/" in token or any(token.endswith(suffix) for suffix in _CODE_SUFFIXES | _DOC_SUFFIXES):
        return _RawRef(heading_path, "file", token, "mentions_file", "HIGH", line, "code-span")
    if _SYMBOLISH_RE.match(token) and len(token) > 2:
        return _RawRef(
            heading_path,
            "symbol",
            token,
            "mentions_symbol",
            "MEDIUM",
            line,
            "code-span",
            "Code-span symbol references are static text evidence, not dependency evidence.",
        )
    return None


def _resolve_ref(
    conn: sqlite3.Connection,
    root: Path,
    ref: _RawRef,
) -> tuple[str | None, str | None, DocConfidence, str | None]:
    if ref.target_kind == "file":
        return _resolve_file(conn, root, ref)
    if ref.target_kind == "symbol":
        return _resolve_symbol(conn, ref)
    if ref.target_kind == "route":
        return _resolve_route(conn, ref)
    if ref.target_kind == "config":
        return _resolve_config(conn, ref)
    if ref.target_kind == "resource":
        return _resolve_resource(conn, ref)
    if ref.target_kind in {"doc", "issue", "pr"}:
        return ref.target_kind, ref.target_value, ref.confidence, ref.caveat
    return None, None, "LOW", "Reference target kind is not resolvable by Seam yet."


def _resolve_file(
    conn: sqlite3.Connection,
    root: Path,
    ref: _RawRef,
) -> tuple[str | None, str | None, DocConfidence, str | None]:
    value = ref.target_value
    abs_path = (root / value).resolve() if not Path(value).is_absolute() else Path(value)
    row = conn.execute("SELECT path FROM files WHERE path = ?", (str(abs_path),)).fetchone()
    if row:
        return "file", str(Path(row["path"]).resolve().relative_to(root)), ref.confidence, ref.caveat
    return None, None, "LOW", "File reference did not resolve to an indexed file."


def _resolve_symbol(
    conn: sqlite3.Connection,
    ref: _RawRef,
) -> tuple[str | None, str | None, DocConfidence, str | None]:
    rows = conn.execute(
        """
        SELECT name, qualified_name FROM symbols
        WHERE name = ? OR qualified_name = ?
        ORDER BY id
        LIMIT 2
        """,
        (ref.target_value, ref.target_value),
    ).fetchall()
    if len(rows) == 1:
        value = rows[0]["qualified_name"] or rows[0]["name"]
        return "symbol", str(value), "HIGH" if ref.confidence == "MEDIUM" else ref.confidence, ref.caveat
    if len(rows) > 1:
        return None, None, "LOW", "Symbol reference is ambiguous across indexed symbols."
    return None, None, "LOW", "Symbol reference did not resolve to an indexed symbol."


def _resolve_route(
    conn: sqlite3.Connection,
    ref: _RawRef,
) -> tuple[str | None, str | None, DocConfidence, str | None]:
    row = conn.execute(
        "SELECT symbol_name FROM routes WHERE path = ? OR normalized_path = ? ORDER BY id LIMIT 1",
        (ref.target_value, ref.target_value),
    ).fetchone()
    if row:
        return "route", row["symbol_name"], ref.confidence, ref.caveat
    return None, None, "LOW", "Route reference did not resolve to an indexed route."


def _resolve_config(
    conn: sqlite3.Connection,
    ref: _RawRef,
) -> tuple[str | None, str | None, DocConfidence, str | None]:
    normalized = ref.target_value.lower().replace("_", ".")
    row = conn.execute(
        """
        SELECT symbol_name FROM config_keys
        WHERE key = ? OR normalized_key = ?
        ORDER BY id LIMIT 1
        """,
        (ref.target_value, normalized),
    ).fetchone()
    if row:
        return "config", row["symbol_name"], ref.confidence, ref.caveat
    return None, None, "LOW", "Config reference did not resolve to indexed config-key evidence."


def _resolve_resource(
    conn: sqlite3.Connection,
    ref: _RawRef,
) -> tuple[str | None, str | None, DocConfidence, str | None]:
    normalized = ref.target_value.lower()
    row = conn.execute(
        """
        SELECT symbol_name FROM resources
        WHERE name = ? OR normalized_name = ?
        ORDER BY id LIMIT 1
        """,
        (ref.target_value, normalized),
    ).fetchone()
    if row:
        return "resource", row["symbol_name"], ref.confidence, ref.caveat
    return None, None, "LOW", "Resource reference did not resolve to indexed resource evidence."
