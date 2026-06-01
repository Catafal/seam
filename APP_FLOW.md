# App Flow — Seam (Phase 0)

> Agent interaction flows. Written from the AI agent's perspective.

---

## Flow 1: First-Time Setup (Developer)

```
Developer opens a new project
    │
    ▼
$ seam init
    │
    ├── Walks directory tree
    ├── Detects language per file (Python, TypeScript/JavaScript)
    ├── Parses each file with tree-sitter
    ├── Extracts: symbols (functions, classes, methods), edges (imports, calls)
    ├── Writes to .seam/seam.db (SQLite + FTS5)
    └── Prints summary: "Indexed 847 files, 12,304 symbols in 8.3s"
    │
    ▼
$ seam start
    │
    ├── Starts file watcher (background daemon)
    ├── Starts MCP server (stdio transport)
    └── MCP tools ready: seam_query, seam_context, seam_search
```

**Developer then adds to their Claude Code MCP config:**
```json
{
  "mcpServers": {
    "seam": {
      "command": "seam",
      "args": ["start", "--stdio"],
      "cwd": "/path/to/project"
    }
  }
}
```

---

## Flow 2: Session Start (Agent)

```
Agent receives task: "Add rate limiting to the API"
    │
    ▼
Agent uses seam_query("rate limiting middleware")
    │
    ├── FTS5 search: finds symbols named 'RateLimiter', 'throttle', etc.
    ├── Graph search: finds symbols in same cluster as 'middleware'
    └── Returns: [{symbol, file, line, snippet, callers_count, callees_count}]
    │
    ▼
Agent narrows to relevant symbol with seam_context("APIMiddleware")
    │
    ├── Returns: callers (what calls this), callees (what this calls)
    ├── File location + line number
    └── Docstring / comment if present
    │
    ▼
Agent has full picture before reading a single file
    Ready to implement without exploration tax
```

---

## Flow 3: File Change (Auto-Sync)

```
Developer saves auth/middleware.py
    │
    ▼
File watcher detects change (FSEvents/inotify)
    │
    ▼
Debounce 500ms (coalesce rapid saves)
    │
    ▼
Re-parse changed file with tree-sitter
    │
    ├── Diff: which symbols added / removed / changed?
    ├── Update seam.db (delete old, insert new)
    └── Update FTS5 index for changed symbols
    │
    ▼
Index fresh — next seam_query returns updated results
```

---

## Flow 4: Finding a Symbol (Agent)

```
Agent wants to understand how authentication works
    │
    ▼
seam_search("authenticate login token")
    │
    └── Returns: top-10 FTS5 matches with snippets
              [{symbol: "authenticate_user", file: "auth/service.py:47", snippet: "..."}]
    │
    ▼
seam_context("authenticate_user")
    │
    └── Returns:
          {
            "symbol": "authenticate_user",
            "file": "auth/service.py",
            "line": 47,
            "docstring": "Validates credentials and returns JWT token",
            "callers": ["LoginHandler.post", "TestAuth.test_valid_login"],
            "callees": ["hash_password", "create_token", "UserRepository.find_by_email"]
          }
    │
    ▼
Agent knows: WHERE it is, WHO calls it, WHAT it calls
No file reads needed to get this structural picture
```

---

## Flow 5: Status Check

```
$ seam status
    │
    └── Prints:
          Project: /Users/jordi/projects/bach
          Database: .seam/seam.db (14.2 MB)
          Indexed: 2026-06-01 14:23:01 (3 minutes ago)
          Files: 1,247 | Symbols: 18,432 | Edges: 41,209
          Languages: Python (892 files), TypeScript (355 files)
          Watcher: running (PID 84201)
          Status: FRESH
```

---

## Error States

| State | User sees | What happens |
|---|---|---|
| Unsupported file type | Silently skipped | Added to .seam/ignored.log |
| Parse error (malformed file) | Warning in seam status | Skipped, rest of index proceeds |
| seam.db locked (concurrent write) | Retry with backoff | Automatic, transparent |
| File watcher loses connection | Warning + restart | Auto-restart with exponential backoff |
| Symbol not found | `{"results": [], "message": "No results for 'X'"}` | Empty results, not an error |
