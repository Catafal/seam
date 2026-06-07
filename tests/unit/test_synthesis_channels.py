"""Unit tests for seam/analysis/synthesis_channels.py — closure-collection + event-emitter channels.

TDD: tests written BEFORE implementation (RED first).

Coverage per channel:

CLOSURE-COLLECTION (channel='closure-collection'):
  CC-POSITIVE:   dispatcher field iterated + element invoked → edge dispatcher→appended-callback
  CC-XFILE:      cross-file pairing — append in file A, forEach-invoke in file B → edge emitted
  CC-NEGATIVE-NO-INVOKE: forEach { print($0) } — NO element invocation → no edge
  CC-NEGATIVE-NO-APPEND: iterated but nothing appended → no edge
  CC-CAP:        more than fanout_cap appended callbacks → excess skipped
  CC-MULTI-FIELD: two distinct fields produce independent edges (no cross-contamination)

EVENT-EMITTER (channel='event-emitter'):
  EE-POSITIVE:   .on('done', handler) paired with .emit('done') → edge source.method→handler
  EE-POSITIVE-SUBSCRIBE: subscribe('click', fn) + emit('click') → edge
  EE-POSITIVE-ADD-LISTENER: addEventListener('data', cb) + emit('data') → edge
  EE-POSITIVE-REGISTER: register('ok', h) + dispatch('ok') → edge
  EE-NEGATIVE-DIFF-EVENT: different event keys → no edge
  EE-NEGATIVE-NO-REGISTRAR: only emit with no registration → no edge
  EE-NEGATIVE-ANON-HANDLER: anonymous/inline handler (not a named function) → no edge
  EE-CAP:        event with too many handlers → skipped

Both channels:
  NEVER-RAISE:   malformed source text does not raise
  CONF:          all synthesized edges have confidence=INFERRED and correct synthesized_by
"""

from typing import Any

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, kind: str = "method") -> dict[str, Any]:
    return {"name": name, "kind": kind}


def _edge(src: str, tgt: str, kind: str, conf: str = "EXTRACTED") -> dict[str, Any]:
    return {"source": src, "target": tgt, "kind": kind, "confidence": conf}


def _call_channels(
    symbols: list[dict],
    edges: list[dict],
    file_sources: dict[str, str],
    fanout_cap: int = 10,
) -> list[dict]:
    """Call synthesize_edges with file_sources — the full signature (slice 2 path)."""
    from seam.analysis.synthesis import synthesize_edges
    return synthesize_edges(symbols, edges, file_sources=file_sources, fanout_cap=fanout_cap)


def _cc_edges(results: list[dict]) -> list[dict]:
    """Filter for closure-collection synthesized edges."""
    return [e for e in results if e.get("synthesized_by") == "closure-collection"]


def _ee_edges(results: list[dict]) -> list[dict]:
    """Filter for event-emitter synthesized edges."""
    return [e for e in results if e.get("synthesized_by") == "event-emitter"]


# ── CLOSURE-COLLECTION positive cases ────────────────────────────────────────


class TestClosureCollectionPositive:
    """closure-collection channel: iterating + invoking field → edges from append sites."""

    def test_swift_foreach_element_invoked(self) -> None:
        """Swift: validators.forEach { $0() } + validators.append(cb) → edge dispatcher→cb."""
        # Swift forEach { $0() } pattern: element is invoked (positive)
        dispatcher_src = """\
class FormValidator {
    var validators: [() -> Void] = []

    func runAll() {
        validators.forEach { $0() }
    }
}
"""
        registrar_src = """\
func setupValidation(validator: FormValidator) {
    validator.validators.append(myCallback)
}

func myCallback() {
    print("validate")
}
"""
        symbols = [
            _sym("FormValidator", "class"),
            _sym("FormValidator.runAll", "method"),
            _sym("setupValidation", "function"),
            _sym("myCallback", "function"),
        ]
        edges: list[dict] = []
        file_sources = {
            "FormValidator.swift": dispatcher_src,
            "setup.swift": registrar_src,
        }
        result = _call_channels(symbols, edges, file_sources, fanout_cap=10)
        cc = _cc_edges(result)
        targets = {(e["source"], e["target"]) for e in cc}
        assert len(cc) > 0, f"Expected closure-collection edges; got none. targets={targets}"
        # The edge should link the dispatcher (FormValidator.runAll or containing class)
        # to the appended callback (myCallback)
        assert any("myCallback" in e["target"] for e in cc), (
            f"Expected myCallback as target; got {targets}"
        )

    def test_js_foreach_invoke_pattern(self) -> None:
        """JS: handlers.forEach(h => h()) + handlers.push(fn) → edge to fn."""
        dispatcher_src = """\
class EventBus {
    constructor() { this.handlers = []; }
    fire() { this.handlers.forEach(h => h()); }
}
"""
        registrar_src = """\
function setup(bus) {
    bus.handlers.push(myHandler);
}
function myHandler() {}
"""
        symbols = [
            _sym("EventBus", "class"),
            _sym("EventBus.fire", "method"),
            _sym("setup", "function"),
            _sym("myHandler", "function"),
        ]
        edges: list[dict] = []
        file_sources = {
            "EventBus.js": dispatcher_src,
            "setup.js": registrar_src,
        }
        result = _call_channels(symbols, edges, file_sources, fanout_cap=10)
        cc = _cc_edges(result)
        assert len(cc) > 0, "Expected closure-collection edges; got none"
        assert any("myHandler" in e["target"] for e in cc), (
            f"Expected myHandler as target; got {[(e['source'], e['target']) for e in cc]}"
        )

    def test_python_append_and_iterate(self) -> None:
        """Python: for cb in callbacks: cb() + callbacks.append(handler) → edge to handler."""
        dispatcher_src = """\
class Dispatcher:
    def __init__(self):
        self.callbacks = []

    def run(self):
        for cb in self.callbacks:
            cb()
"""
        registrar_src = """\
def register(d):
    d.callbacks.append(my_handler)

def my_handler():
    pass
"""
        symbols = [
            _sym("Dispatcher", "class"),
            _sym("Dispatcher.run", "method"),
            _sym("register", "function"),
            _sym("my_handler", "function"),
        ]
        edges: list[dict] = []
        file_sources = {
            "dispatcher.py": dispatcher_src,
            "register.py": registrar_src,
        }
        result = _call_channels(symbols, edges, file_sources, fanout_cap=10)
        cc = _cc_edges(result)
        assert len(cc) > 0, "Expected closure-collection edges for Python for-loop invoke"
        assert any("my_handler" in e["target"] for e in cc), (
            f"Expected my_handler as target; got {[(e['source'], e['target']) for e in cc]}"
        )

    def test_cross_file_pairing(self) -> None:
        """Cross-file: append in file A, forEach-invoke in file B → edge emitted."""
        source_a = """\
// Appender file
manager.listeners.append(alertCallback)
func alertCallback() {}
"""
        source_b = """\
// Dispatcher file
class Manager {
    var listeners: [() -> Void] = []
    func notifyAll() {
        listeners.forEach { $0() }
    }
}
"""
        symbols = [
            _sym("Manager", "class"),
            _sym("Manager.notifyAll", "method"),
            _sym("alertCallback", "function"),
        ]
        edges: list[dict] = []
        file_sources = {"appender.swift": source_a, "Manager.swift": source_b}
        result = _call_channels(symbols, edges, file_sources, fanout_cap=10)
        cc = _cc_edges(result)
        assert len(cc) > 0, "Expected cross-file closure-collection edge; got none"
        assert any("alertCallback" in e["target"] for e in cc), (
            f"Expected alertCallback as target; got {[(e['source'], e['target']) for e in cc]}"
        )


# ── CLOSURE-COLLECTION negative cases ────────────────────────────────────────


class TestClosureCollectionNegative:
    """Critical negatives: forEach without element invocation must emit NOTHING."""

    def test_foreach_without_invocation_emits_nothing(self) -> None:
        """CRITICAL: forEach { print($0) } — element NOT invoked → no edge.

        This is the most important negative case from the PRD: a forEach that
        iterates but does NOT call the element must produce zero synthesized edges.
        """
        dispatcher_src = """\
class Logger {
    var messages: [String] = []
    func printAll() {
        messages.forEach { print($0) }
    }
}
"""
        registrar_src = """\
func addMessage(logger: Logger) {
    logger.messages.append("hello")
}
"""
        symbols = [
            _sym("Logger", "class"),
            _sym("Logger.printAll", "method"),
            _sym("addMessage", "function"),
        ]
        file_sources = {
            "Logger.swift": dispatcher_src,
            "add.swift": registrar_src,
        }
        result = _call_channels(symbols, [], file_sources)
        cc = _cc_edges(result)
        assert cc == [], (
            f"CRITICAL: forEach without element invocation must emit NO closure-collection edges; "
            f"got {cc}"
        )

    def test_no_append_produces_no_edge(self) -> None:
        """Iterated + element invoked, but nothing appended → no edge (no registration site)."""
        src = """\
class Bus {
    var handlers: [() -> Void] = []
    func fire() { handlers.forEach { $0() } }
}
"""
        symbols = [_sym("Bus", "class"), _sym("Bus.fire", "method")]
        result = _call_channels(symbols, [], {"bus.swift": src})
        cc = _cc_edges(result)
        assert cc == [], (
            f"No append site → no closure-collection edge; got {cc}"
        )

    def test_no_iteration_produces_no_edge(self) -> None:
        """Append site exists but no iteration/invocation → no edge."""
        src = """\
class Bus {
    var handlers: [() -> Void] = []
}
func setup(b: Bus) { b.handlers.append(myFn) }
func myFn() {}
"""
        symbols = [
            _sym("Bus", "class"),
            _sym("setup", "function"),
            _sym("myFn", "function"),
        ]
        result = _call_channels(symbols, [], {"bus.swift": src})
        cc = _cc_edges(result)
        assert cc == [], (
            f"No iteration/invocation site → no closure-collection edge; got {cc}"
        )

    def test_different_field_names_no_cross_contamination(self) -> None:
        """Field 'fooCallbacks' and 'barCallbacks' are different — no cross-pairing."""
        src = """\
class Bus {
    var fooCallbacks: [() -> Void] = []
    var barCallbacks: [() -> Void] = []

    func runFoo() { fooCallbacks.forEach { $0() } }
}
func addBar(b: Bus) { b.barCallbacks.append(barFn) }
func barFn() {}
"""
        symbols = [
            _sym("Bus", "class"),
            _sym("Bus.runFoo", "method"),
            _sym("addBar", "function"),
            _sym("barFn", "function"),
        ]
        result = _call_channels(symbols, [], {"bus.swift": src})
        cc = _cc_edges(result)
        # barFn is appended to barCallbacks; runFoo iterates fooCallbacks → different fields
        assert not any("barFn" in e["target"] for e in cc), (
            f"barFn must not appear as target of fooCallbacks dispatch; got {cc}"
        )


# ── CLOSURE-COLLECTION fanout cap ────────────────────────────────────────────


class TestClosureCollectionCap:
    """closure-collection channel respects fanout_cap."""

    def test_cap_limits_edges(self) -> None:
        """When 10 functions are appended to the same collection, cap=3 limits to ≤3 edges."""
        dispatcher_src = """\
class Bus {
    var cbs: [() -> Void] = []
    func fire() { cbs.forEach { $0() } }
}
"""
        # Build appender source that appends 10 different callbacks
        appender_lines = []
        for i in range(10):
            appender_lines.append(f"func cb{i}() {{}}")
            appender_lines.append(f"bus.cbs.append(cb{i})")
        appender_src = "\n".join(appender_lines)

        symbols = [
            _sym("Bus", "class"),
            _sym("Bus.fire", "method"),
        ] + [_sym(f"cb{i}", "function") for i in range(10)]

        file_sources = {
            "Bus.swift": dispatcher_src,
            "appenders.swift": appender_src,
        }
        result = _call_channels(symbols, [], file_sources, fanout_cap=3)
        cc = _cc_edges(result)
        assert len(cc) <= 3, (
            f"Expected ≤3 edges with fanout_cap=3; got {len(cc)}: {[(e['source'], e['target']) for e in cc]}"
        )


# ── EVENT-EMITTER positive cases ──────────────────────────────────────────────


class TestEventEmitterPositive:
    """event-emitter channel: registrar+dispatcher verb pairing by event string key."""

    def test_on_and_emit_basic(self) -> None:
        """JS: .on('done', handler) + .emit('done') → edge with channel='event-emitter'."""
        src = """\
class EventEmitter {
    on(event, handler) { this._listeners[event] = handler; }
    emit(event) { this._listeners[event](); }
}

function handleDone() { console.log("done"); }

emitter.on('done', handleDone);
emitter.emit('done');
"""
        symbols = [
            _sym("EventEmitter", "class"),
            _sym("EventEmitter.on", "method"),
            _sym("EventEmitter.emit", "method"),
            _sym("handleDone", "function"),
        ]
        result = _call_channels(symbols, [], {"emitter.js": src})
        ee = _ee_edges(result)
        assert len(ee) > 0, "Expected event-emitter edges; got none"
        assert any("handleDone" in e["target"] for e in ee), (
            f"Expected handleDone as target; got {[(e['source'], e['target']) for e in ee]}"
        )

    def test_subscribe_and_emit(self) -> None:
        """subscribe('click', fn) + emit('click') → edge."""
        src = """\
bus.subscribe('click', onClickHandler);
bus.emit('click');
function onClickHandler() {}
"""
        symbols = [
            _sym("onClickHandler", "function"),
        ]
        result = _call_channels(symbols, [], {"bus.js": src})
        ee = _ee_edges(result)
        assert len(ee) > 0, "Expected event-emitter edge for subscribe/emit; got none"
        assert any("onClickHandler" in e["target"] for e in ee), (
            f"Expected onClickHandler; got {[(e['source'], e['target']) for e in ee]}"
        )

    def test_add_listener_and_emit(self) -> None:
        """addListener('data', cb) + emit('data') → edge."""
        src = """\
socket.addListener('data', dataHandler);
socket.emit('data');
function dataHandler() {}
"""
        symbols = [_sym("dataHandler", "function")]
        result = _call_channels(symbols, [], {"socket.js": src})
        ee = _ee_edges(result)
        assert len(ee) > 0, "Expected event-emitter edge for addListener/emit; got none"
        assert any("dataHandler" in e["target"] for e in ee), (
            f"Expected dataHandler; got {[(e['source'], e['target']) for e in ee]}"
        )

    def test_register_and_dispatch(self) -> None:
        """register('ok', h) + dispatch('ok') → edge."""
        src = """\
mgr.register('ok', successHandler);
mgr.dispatch('ok');
function successHandler() {}
"""
        symbols = [_sym("successHandler", "function")]
        result = _call_channels(symbols, [], {"mgr.js": src})
        ee = _ee_edges(result)
        assert len(ee) > 0, "Expected event-emitter edge for register/dispatch; got none"
        assert any("successHandler" in e["target"] for e in ee), (
            f"Expected successHandler; got {[(e['source'], e['target']) for e in ee]}"
        )

    def test_on_uppercase_event_verb(self) -> None:
        """onDone style registration with emit('Done') → edge."""
        src = """\
emitter.onDone(completionHandler);
emitter.emit('Done');
function completionHandler() {}
"""
        symbols = [_sym("completionHandler", "function")]
        result = _call_channels(symbols, [], {"e.js": src})
        ee = _ee_edges(result)
        # onDone matches on[A-Z]\w* pattern
        assert len(ee) > 0, "Expected event-emitter edge for onDone/emit; got none"
        assert any("completionHandler" in e["target"] for e in ee), (
            f"Expected completionHandler; got {[(e['source'], e['target']) for e in ee]}"
        )

    def test_synthesized_edge_fields(self) -> None:
        """Event-emitter edges must have kind='call', confidence='INFERRED', synthesized_by='event-emitter'."""
        src = """\
bus.on('go', goHandler);
bus.emit('go');
function goHandler() {}
"""
        symbols = [_sym("goHandler", "function")]
        result = _call_channels(symbols, [], {"bus.js": src})
        ee = _ee_edges(result)
        assert ee, "Expected at least one event-emitter edge"
        for e in ee:
            assert e["kind"] == "call", f"Expected kind=call; got {e['kind']}"
            assert e["confidence"] == "INFERRED", f"Expected INFERRED; got {e['confidence']}"
            assert e["synthesized_by"] == "event-emitter"


# ── EVENT-EMITTER negative cases ──────────────────────────────────────────────


class TestEventEmitterNegative:
    """Event-emitter channel must NOT emit edges for these patterns."""

    def test_different_event_keys_no_edge(self) -> None:
        """on('done', h) + emit('start') — different event strings → no edge."""
        src = """\
emitter.on('done', doneHandler);
emitter.emit('start');
function doneHandler() {}
"""
        symbols = [_sym("doneHandler", "function")]
        result = _call_channels(symbols, [], {"e.js": src})
        ee = _ee_edges(result)
        assert ee == [], (
            f"Different event keys must not produce edge; got {ee}"
        )

    def test_no_registrar_no_edge(self) -> None:
        """emit('x') with no registration call → no edge."""
        src = """\
emitter.emit('x');
function xHandler() {}
"""
        symbols = [_sym("xHandler", "function")]
        result = _call_channels(symbols, [], {"e.js": src})
        ee = _ee_edges(result)
        assert ee == [], f"emit with no registrar → no edge; got {ee}"

    def test_anonymous_inline_handler_no_edge(self) -> None:
        """on('click', function() {}) — anonymous handler → no edge (not a named function)."""
        src = """\
emitter.on('click', function() { doSomething(); });
emitter.emit('click');
"""
        symbols: list[dict] = []
        result = _call_channels(symbols, [], {"e.js": src})
        ee = _ee_edges(result)
        assert ee == [], (
            f"Anonymous inline handler must not produce edge; got {ee}"
        )

    def test_no_dispatcher_no_edge(self) -> None:
        """on('ready', handler) with no emit/dispatch → no edge."""
        src = """\
emitter.on('ready', readyHandler);
function readyHandler() {}
"""
        symbols = [_sym("readyHandler", "function")]
        result = _call_channels(symbols, [], {"e.js": src})
        ee = _ee_edges(result)
        assert ee == [], f"Registration without dispatch → no edge; got {ee}"


# ── EVENT-EMITTER fanout cap ──────────────────────────────────────────────────


class TestEventEmitterCap:
    """event-emitter channel respects per-event fanout cap."""

    def test_cap_limits_handlers_per_event(self) -> None:
        """When event 'x' has 10 handlers registered, cap=3 limits to ≤3 edges emitted."""
        # Build source with 10 registrations for the same event key
        lines = []
        for i in range(10):
            lines.append(f"emitter.on('x', handler{i});")
            lines.append(f"function handler{i}() {{}}")
        lines.append("emitter.emit('x');")
        src = "\n".join(lines)

        symbols = [_sym(f"handler{i}", "function") for i in range(10)]
        result = _call_channels(symbols, [], {"e.js": src}, fanout_cap=3)
        ee = _ee_edges(result)
        assert len(ee) <= 3, (
            f"Expected ≤3 edges with fanout_cap=3; got {len(ee)}: "
            f"{[(e['source'], e['target']) for e in ee]}"
        )


# ── Integration: both channels coexist ────────────────────────────────────────


class TestChannelCoexistence:
    """Both channels can emit edges from the same synthesize_edges call."""

    def test_both_channels_in_one_call(self) -> None:
        """A single source file with both patterns → edges from both channels."""
        src = """\
class App {
    var validators: [() -> Void] = []

    func runValidators() { validators.forEach { $0() } }

    func on(event, handler) { self._listeners[event] = handler; }
    func emit(event) { self._listeners[event]?(); }
}

function checkInput() {}
App().validators.append(checkInput)

App().on('save', onSaveHandler)
App().emit('save')
function onSaveHandler() {}
"""
        symbols = [
            _sym("App", "class"),
            _sym("App.runValidators", "method"),
            _sym("App.on", "method"),
            _sym("App.emit", "method"),
            _sym("checkInput", "function"),
            _sym("onSaveHandler", "function"),
        ]
        result = _call_channels(symbols, [], {"app.swift": src})
        cc = _cc_edges(result)
        ee = _ee_edges(result)
        # At least one closure-collection edge (checkInput) and one event-emitter edge (onSaveHandler)
        assert len(cc) > 0, f"Expected closure-collection edge; got none from {[e['synthesized_by'] for e in result]}"
        assert len(ee) > 0, "Expected event-emitter edge; got none"


# ── Never raises ─────────────────────────────────────────────────────────────


class TestChannelsNeverRaise:
    """Channels never raise on malformed/unexpected source text."""

    def test_malformed_source_text_does_not_raise(self) -> None:
        """Binary noise / encoding errors in source text → returns [] without raising."""
        from seam.analysis.synthesis import synthesize_edges

        bad_src = {"file.swift": "\x00\x01\x02 invalid \xff\xfe"}
        try:
            result = synthesize_edges([], [], file_sources=bad_src, fanout_cap=10)
            assert isinstance(result, list)
        except Exception as exc:
            raise AssertionError(
                f"synthesize_edges raised on malformed source: {type(exc).__name__}: {exc}"
            ) from exc

    def test_empty_source_text_does_not_raise(self) -> None:
        """Empty string for a source file → returns [] without raising."""
        from seam.analysis.synthesis import synthesize_edges

        result = synthesize_edges([], [], file_sources={"empty.swift": ""}, fanout_cap=10)
        assert isinstance(result, list)

    def test_none_like_file_sources_key_does_not_raise(self) -> None:
        """Dict with unusual key → no crash."""
        from seam.analysis.synthesis import synthesize_edges

        result = synthesize_edges([], [], file_sources={"": "class Foo {}"}, fanout_cap=10)
        assert isinstance(result, list)
