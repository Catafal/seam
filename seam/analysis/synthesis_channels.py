"""Edge-synthesis channels for A1 dynamic-dispatch patterns.

LAYER: leaf — imports only stdlib + seam.config. No DB access.

This module implements the source-text-based synthesis channels:
  A1a — closure-collection dispatch: a collection field that holds closures is
         iterated AND the element invoked; paired globally (cross-file) by
         collection/field name with sites that APPEND a closure.
  A1b — EventEmitter/observer dispatch: registrar verbs (on[A-Z][A-Za-z]*, subscribe,
         addListener, addEventListener, register, watch, listen, addCallback)
         paired with dispatcher verbs (emit, trigger, notify, dispatch, fire,
         publish, flush) keyed by event-string literal.

Design rules (shared with synthesis.py):
  - All public functions are PURE: no side effects, deterministic output.
  - Never raise: internal errors degrade to "no edge emitted" (same contract as
    parsers). Caller (synthesis.py) has its own outer try/except guard.
  - Cap-bounded: fanout_cap limits edges per collection/event to prevent graph
    explosions on widely-shared field names.
  - Pairing uses string names — no node IDs. Mirrors the rest of Seam's edge model.
  - Only NAMED handlers/callbacks produce edges (inline/anonymous lambdas are skipped
    as they are not addressable by the index). This is the conservatism contract.

WHY source-text-based: the extractor already captures the AST-derived symbol/edge
  graph. The patterns here (iteration + element invocation, event string matching) are
  NOT reliably captured by the tree-sitter extractor (they require data-flow awareness).
  Regex scanning of source text is sufficient for the patterns in scope and avoids
  re-running the parser in the synthesis pass.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Channel identifiers (stored in edges.synthesized_by) ─────────────────────
CHANNEL_CLOSURE_COLLECTION = "closure-collection"
CHANNEL_EVENT_EMITTER = "event-emitter"


# ─────────────────────────────────────────────────────────────────────────────
# A1a — Closure-Collection Dispatch
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that match "collection iterated AND element INVOKED":
#   forEach { $0() }          — Swift $0 invocation
#   forEach { it() }          — Kotlin it invocation
#   forEach(h => h())         — JS/TS arrow invoke
#   forEach(fn)               — passing a bare invoker? NOT matched (no evidence of invocation)
#   for cb in cbs: cb()       — Python for-loop with call on next line / same expr
#   for h in handlers: h()    — same
# Critically EXCLUDED:
#   forEach { print($0) }     — $0 not invoked (arg to another fn, not called itself)
#   forEach { it.process() }  — it.method() is a method call on the element, not element()
#   map { $0.value }          — field access, no invocation

# Swift/Kotlin: <fieldName>.forEach { $0() } or { it() }
# The trailing `()` is the load-bearing gate: it requires the element to be INVOKED,
# not merely referenced. `forEach { print($0) }` passes $0 as an argument (no `()`
# directly after it) and so emits nothing — only genuine element invocation is dispatch.
_RE_CC_INVOKE_SWIFT_KT = re.compile(
    r"(?<!\w)([\w]+)\s*\.\s*forEach\s*\{\s*(?:\$0|it)\s*\(\s*\)",
    re.MULTILINE,
)

# Regex for JS/TS: <field>.forEach(h => h()) or .forEach((h) => h())
_RE_CC_INVOKE_JS_ARROW = re.compile(
    r"(?<!\w)([\w]+)\s*\.\s*forEach\s*\(\s*(?:\w+|\(\w+\))\s*=>\s*(?:\w+|\(\w+\))\s*\(\s*\)",
    re.MULTILINE,
)

# Regex for Python: for <var> in <field>:\n    <var>() — variable invoked directly
# We match "for VARNAME in FIELDNAME:" followed by VARNAME()
_RE_CC_INVOKE_PY = re.compile(
    r"for\s+(\w+)\s+in\s+(?:self\.)?(\w+)\s*:\s*\n(?:[ \t]+)\1\s*\(",
    re.MULTILINE,
)

# Regex for field.append/push/add/insert/addCallback with a NAMED (non-lambda) target:
#   field.append(myCallback) / field.push(handler) / field.add(fn) ...
# We require the argument to be a BARE IDENTIFIER (no parens = not an inline call,
# no lambda keyword, no curly braces).
# Matches: <obj>.<field>.<verb>(identifier) OR <field>.<verb>(identifier)
_RE_CC_APPEND = re.compile(
    r"(?:[\w]+\s*\.\s*)?([\w]+)\s*\.\s*(?:append|push|add|insert|addCallback|enqueue)\s*\(\s*([A-Za-z_]\w*)\s*\)",
    re.MULTILINE,
)

# Companion to _RE_CC_APPEND for the bare `field.append(fn)` form (no `obj.` prefix).
# The negative lookbehind `(?<!\.)` ensures we don't double-match the tail of an
# `obj.field.append(...)` expression already captured by _RE_CC_APPEND above.
_RE_CC_APPEND_BARE = re.compile(
    r"(?<!\.)(\w+)\s*\.\s*(?:append|push|add|insert)\s*\(\s*([A-Za-z_]\w*)\s*\)",
    re.MULTILINE,
)


def _find_iteration_fields(sources: dict[str, str]) -> set[str]:
    """Return field names that are iterated AND element is invoked (across all source files).

    These are the DISPATCHER-side collection names.
    """
    fields: set[str] = set()
    for source_text in sources.values():
        try:
            for m in _RE_CC_INVOKE_SWIFT_KT.finditer(source_text):
                fields.add(m.group(1))
            for m in _RE_CC_INVOKE_JS_ARROW.finditer(source_text):
                fields.add(m.group(1))
            # Python: match captures (loop_var, field_name) — field is group 2
            for m in _RE_CC_INVOKE_PY.finditer(source_text):
                fields.add(m.group(2))
        except Exception:  # noqa: BLE001
            continue
    return fields


def _find_append_sites(
    sources: dict[str, str],
    dispatch_fields: set[str],
    known_symbol_names: set[str],
) -> dict[str, list[str]]:
    """Return mapping field_name → list[callback_name] from append sites.

    Only NAMED functions/methods that exist in the symbol index are included
    (conservatism: unnamed callbacks / lambdas are not indexable targets).
    """
    field_to_callbacks: dict[str, list[str]] = {}
    for source_text in sources.values():
        try:
            for m in _RE_CC_APPEND.finditer(source_text):
                field = m.group(1)
                callback = m.group(2)
                if field not in dispatch_fields:
                    continue
                # Only emit edges to symbols actually in the index.
                if callback not in known_symbol_names:
                    continue
                field_to_callbacks.setdefault(field, []).append(callback)
            for m in _RE_CC_APPEND_BARE.finditer(source_text):
                field = m.group(1)
                callback = m.group(2)
                if field not in dispatch_fields:
                    continue
                if callback not in known_symbol_names:
                    continue
                field_to_callbacks.setdefault(field, []).append(callback)
        except Exception:  # noqa: BLE001
            continue
    return field_to_callbacks


def run_closure_collection_channel(
    symbols: list[dict[str, Any]],
    file_sources: dict[str, str],
    fanout_cap: int,
) -> list[dict[str, Any]]:
    """A1a: closure-collection dispatch channel.

    Algorithm:
    1. Scan all source files for "collection iterated AND element invoked" patterns
       → these are the dispatcher-side field names.
    2. Scan all source files for "field.append(namedCallback)" patterns on those fields
       → these are the registration sites.
    3. For each (field, callback) pair found, emit a synthesized call edge.
    4. Apply fanout_cap per field.

    The edge source is the dispatch field name (the collection); the target is the
    named callback that was appended to it.

    Never raises — all errors degrade to "no edge emitted for this file/pattern".
    """
    if not file_sources:
        return []

    result: list[dict[str, Any]] = []
    try:
        # Build the set of names the index knows (qualified + bare parts).
        known_names = _build_known_names(symbols)

        # Step 1: Find all dispatcher-side collection fields.
        dispatch_fields = _find_iteration_fields(file_sources)
        if not dispatch_fields:
            return []

        # Step 2: Find append sites for those fields.
        field_callbacks = _find_append_sites(file_sources, dispatch_fields, known_names)
        if not field_callbacks:
            return []

        # Step 3: Emit edges, deduplicated, cap-bounded.
        for field_name in sorted(field_callbacks):
            callbacks = sorted(set(field_callbacks[field_name]))  # dedup + determinism
            # Cap by TRUNCATING (vs the event-emitter channel, which drops the whole
            # event): an over-subscribed collection still has a few genuine dispatch
            # targets worth keeping, whereas a 100-handler event key is more likely a
            # false-positive string collision. Different precision/recall trade per channel.
            if fanout_cap > 0:
                callbacks = callbacks[:fanout_cap]

            for cb_name in callbacks:
                # Find the fully-qualified target name (prefer qualified over bare).
                target = _resolve_name(cb_name, symbols)
                result.append({
                    "source": field_name,
                    "target": target,
                    "kind": "call",
                    "confidence": "INFERRED",
                    "synthesized_by": CHANNEL_CLOSURE_COLLECTION,
                    # Not file-scoped — the bridge supplies file_id + line at persist time.
                    "line": 0,
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "synthesis: closure-collection channel failed (%s: %s) — partial results",
            type(exc).__name__,
            exc,
        )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# A1b — EventEmitter / Observer Dispatch
# ─────────────────────────────────────────────────────────────────────────────

# Registrar verbs: match on[A-Z][A-Za-z]* or the listed explicit words.
# We capture: .on('eventKey', namedHandler) or .subscribe('key', fn) etc.
# Groups: (event_key, handler_name)
_RE_EE_REGISTRAR = re.compile(
    r"\.\s*(?:on(?=[A-Z][A-Za-z]*\s*\()|on\b|subscribe\b|addListener\b|addEventListener\b"
    r"|register\b|watch\b|listen\b|addCallback\b)"
    r"\s*\(\s*['\"]([A-Za-z_][\w.-]*)['\"]"  # event string key
    r"\s*,\s*"
    r"([A-Za-z_]\w*)"  # named handler identifier (bare — not a lambda/anonymous)
    r"\s*\)",
    re.MULTILINE,
)

# Special: onEventName(handler) — no event string, name from the verb itself
# e.g. emitter.onDone(completionHandler) → key='Done', handler='completionHandler'
_RE_EE_ON_VERB = re.compile(
    r"\.\s*(on([A-Z][A-Za-z]*))\s*\(\s*([A-Za-z_]\w*)\s*\)",
    re.MULTILINE,
)

# Dispatcher verbs: .emit('key') / .trigger('key') etc.
# Group: (event_key)
_RE_EE_DISPATCHER = re.compile(
    r"\.\s*(?:emit|trigger|notify|dispatch|fire|publish|flush)\s*\(\s*['\"]([A-Za-z_][\w.-]*)['\"]",
    re.MULTILINE,
)


def _collect_registrations(sources: dict[str, str]) -> dict[str, list[str]]:
    """Scan sources for registrar calls → mapping event_key → list[handler_name]."""
    event_to_handlers: dict[str, list[str]] = {}
    for source_text in sources.values():
        try:
            # Standard: .on('key', handler) / .subscribe('key', fn) etc.
            for m in _RE_EE_REGISTRAR.finditer(source_text):
                event_key = m.group(1)
                handler = m.group(2)
                if _is_anonymous(handler):
                    continue
                event_to_handlers.setdefault(event_key, []).append(handler)

            # on[A-Z]Verb style: .onDone(handler) — key derived from verb name
            for m in _RE_EE_ON_VERB.finditer(source_text):
                # group(1)=full verb 'onDone', group(2)=bare key 'Done', group(3)=handler
                event_key = m.group(2)  # e.g. 'Done'
                handler = m.group(3)
                if _is_anonymous(handler):
                    continue
                event_to_handlers.setdefault(event_key, []).append(handler)
        except Exception:  # noqa: BLE001
            continue
    return event_to_handlers


def _collect_dispatch_keys(sources: dict[str, str]) -> set[str]:
    """Scan sources for dispatcher calls → set of dispatched event keys."""
    keys: set[str] = set()
    for source_text in sources.values():
        try:
            for m in _RE_EE_DISPATCHER.finditer(source_text):
                keys.add(m.group(1))
        except Exception:  # noqa: BLE001
            continue
    return keys


def _is_anonymous(name: str) -> bool:
    """Return True if name looks like an anonymous/inline expression, not a bare identifier.

    A bare identifier is all word chars [A-Za-z_][A-Za-z0-9_]*. Anything with parens, braces,
    arrows, lambda keywords, etc. is anonymous.
    """
    return not re.fullmatch(r"[A-Za-z_]\w*", name)


def run_event_emitter_channel(
    symbols: list[dict[str, Any]],
    file_sources: dict[str, str],
    fanout_cap: int,
) -> list[dict[str, Any]]:
    """A1b: EventEmitter/observer dispatch channel.

    Algorithm:
    1. Scan all source files for registrar calls → event_key → [handler_name].
    2. Scan all source files for dispatcher calls → set of dispatched event keys.
    3. For each event_key dispatched that has registered handlers:
       a. Filter: only handlers whose name appears in the symbol index.
       b. Apply fanout_cap: skip event if handler count > fanout_cap.
       c. Emit synthesized call edges (source=event_key, target=handler_name).

    The edge source is the event key (a string constant, not a symbol name). This is
    intentional: the dispatcher is not necessarily a single method but an event concept.
    Using the event key as source keeps the edge within the string-name model and lets
    callers of seam_impact target the event key directly.

    Never raises — all errors degrade to "no edge emitted for this file/pattern".
    """
    if not file_sources:
        return []

    result: list[dict[str, Any]] = []
    try:
        # Build the set of names the index knows (qualified + bare parts).
        known_names = _build_known_names(symbols)

        # Step 1: Collect registrations.
        event_to_handlers = _collect_registrations(file_sources)
        if not event_to_handlers:
            return []

        # Step 2: Collect dispatched keys.
        dispatch_keys = _collect_dispatch_keys(file_sources)
        if not dispatch_keys:
            return []

        # Step 3: Emit edges for matched (event_key, handler) pairs.
        for event_key in sorted(dispatch_keys):
            raw_handlers = event_to_handlers.get(event_key, [])
            if not raw_handlers:
                continue

            # Filter to only named/indexed handlers; dedup; sort for determinism.
            handlers = sorted(set(
                h for h in raw_handlers
                if not _is_anonymous(h) and (h in known_names or not known_names)
            ))

            if not handlers:
                continue

            # Apply fanout_cap: if too many handlers, skip this event entirely
            # (a generic event with 100 handlers is likely a false positive pattern).
            if fanout_cap > 0 and len(handlers) > fanout_cap:
                logger.debug(
                    "synthesis: event-emitter fanout_cap=%d exceeded for event '%s' "
                    "(%d handlers) — skipping",
                    fanout_cap,
                    event_key,
                    len(handlers),
                )
                continue

            for handler in handlers:
                target = _resolve_name(handler, symbols)
                result.append({
                    "source": event_key,
                    "target": target,
                    "kind": "call",
                    "confidence": "INFERRED",
                    "synthesized_by": CHANNEL_EVENT_EMITTER,
                    # Not file-scoped — the bridge supplies file_id + line at persist time.
                    "line": 0,
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "synthesis: event-emitter channel failed (%s: %s) — partial results",
            type(exc).__name__,
            exc,
        )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_known_names(symbols: list[dict[str, Any]]) -> set[str]:
    """Return all indexed symbol names plus their bare (post-last-dot) forms.

    Both source-text channels filter synthesized targets to names the index
    actually knows (conservatism: never emit an edge to an unknown identifier).
    Adding the bare suffix lets a simple identifier in source ('myHandler') match
    a qualified symbol ('Class.myHandler').
    """
    known: set[str] = set()
    for sym in symbols:
        name = sym.get("name", "") if isinstance(sym, dict) else ""
        if not name:
            continue
        known.add(name)
        if "." in name:
            known.add(name.split(".")[-1])
    return known


def _resolve_name(bare_name: str, symbols: list[dict[str, Any]]) -> str:
    """Prefer the qualified name (Class.method) over the bare name if a unique match exists.

    If exactly one symbol has the bare name as its suffix (or equals it), return that
    qualified name. Otherwise return the bare name as-is (string-name-keyed contract).

    WHY: synthesis channels often find bare function/callback names from source text.
    Returning the qualified name where unambiguous gives downstream seam_impact/context
    a better match against the symbol index.
    """
    matches = []
    for sym in symbols:
        name = sym.get("name", "") if isinstance(sym, dict) else ""
        if not name:
            continue
        if name == bare_name or name.endswith(f".{bare_name}"):
            matches.append(name)
    if len(matches) == 1:
        return matches[0]
    # Ambiguous or not found — keep the bare name.
    return bare_name
