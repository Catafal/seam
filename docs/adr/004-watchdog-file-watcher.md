# ADR-004: watchdog for File Watching

## Status
Accepted — 2026-06-01

## Context
Seam needs to watch source files for changes and trigger incremental re-indexing automatically. Options:

- **watchdog** — Python library wrapping FSEvents (macOS), inotify (Linux), ReadDirectoryChangesW (Windows). Mature (2011), 10k+ stars. API: EventHandler subclass.
- **watchfiles** — Newer Rust-backed Python library. Faster event delivery. Simpler API (async generator). Less ecosystem support.
- **polling** — OS-agnostic but high CPU; unacceptable for always-on background process.
- **Rolling our own FSEvents binding** — No.

## Decision
**watchdog.**

Specific reasons:
1. Mature API with good cross-platform track record (macOS + Linux are the primary targets).
2. EventHandler subclass pattern maps cleanly to `SeamWatcher(FileSystemEventHandler)`.
3. `watchdog>=4.0.0` supports Python 3.12+ cleanly.
4. Debouncing is straightforward with a threading.Timer in the event handler.

## Alternatives Rejected
- **watchfiles:** Excellent performance, but async API adds complexity to the MCP server's threading model. Revisit for Phase 2 if watcher performance is a bottleneck.
- **polling:** CPU cost is unacceptable for an always-on background process.

## Consequences
- Watcher runs in a background thread (not a subprocess) alongside the MCP server's event loop.
- Debounce implemented with `threading.Timer` — cancel + restart on rapid successive events.
- On macOS, FSEvents coalesces events; rapid saves within 500ms may be delivered as a single event (desired behavior).
- Cross-platform: tested on macOS (FSEvents) + Linux (inotify). Windows is untested but watchdog supports it.
