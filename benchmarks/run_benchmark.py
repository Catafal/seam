"""Seam benchmark harness — static retrieval-context proxy (IMPLEMENTATION_PLAN steps 9.1-9.3).

WHAT THIS MEASURES (and what it does NOT)
------------------------------------------
The Seam thesis (DISCOVERY.md): AI agents waste tokens re-discovering codebase
structure with grep + file reads every session; Seam lets them *query* instead.

This harness measures a **static proxy** for that cost: for a fixed set of realistic
codebase-comprehension questions, how much CONTEXT (text the model must ingest) does
each approach yield?

  - BASELINE  = what an agent consumes using only `grep` + reading source.
                Two variants are reported so the result is honest, not rigged:
                  * whole-file : full content of every file that has >=1 grep match
                                 (Claude Code's Read tool reads whole files by default —
                                  this is the dominant real behavior, an UPPER bound).
                  * windowed   : only +/- WINDOW lines around each grep match
                                 (a conservative LOWER bound).
  - SEAM      = the exact JSON the corresponding MCP tool handler returns — the same
                bytes an agent receives over stdio.

This is NOT a live end-to-end agent-session token A/B on the Bach project (steps 9.1/9.2
as originally framed). That gold-standard measurement requires running two real agent
sessions and reading provider token meters — it cannot be produced autonomously or
honestly fabricated here. This proxy is fully reproducible (`python benchmarks/run_benchmark.py`)
and measures the load-bearing quantity directly: retrieval-context size per question.

TOKEN ESTIMATE
--------------
tiktoken is not a project dependency (zero-dep ethos), so tokens are ESTIMATED as
chars / 4 (the common GPT heuristic). Raw character counts are reported alongside so
the estimate is auditable. The reduction RATIO is near-invariant to the divisor.
"""

import json
import subprocess
import sys
from pathlib import Path

# Import the real MCP tool handlers — the exact code path agents hit.
from seam.indexer.db import connect, init_db
from seam.server.tools import (
    handle_seam_clusters,
    handle_seam_context,
    handle_seam_impact,
    handle_seam_search,
    handle_seam_trace,
)

# ── Config ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCH_DIR = REPO_ROOT / "seam"  # restrict grep to source (an agent greps the codebase)
WINDOW = 25  # lines above/below a grep match for the conservative baseline
CHARS_PER_TOKEN = 4  # documented estimate divisor


def est_tokens(text: str) -> int:
    """Estimated token count (chars / 4 heuristic). See module docstring."""
    return len(text) // CHARS_PER_TOKEN


# ── Baseline: what grep + reading source costs ─────────────────────────────────


def _grep(pattern: str) -> list[tuple[Path, int]]:
    """Return (file, line) for every match of an extended-regex pattern under SEARCH_DIR.

    Mirrors what an agent does first: `grep -rnE pattern seam/`. Skips caches.
    """
    proc = subprocess.run(
        ["grep", "-rnE", "--include=*.py", pattern, str(SEARCH_DIR)],
        capture_output=True,
        text=True,
        check=False,
    )
    hits: list[tuple[Path, int]] = []
    for raw in proc.stdout.splitlines():
        # grep -rn format: path:lineno:content
        parts = raw.split(":", 2)
        if len(parts) >= 2 and parts[1].isdigit():
            hits.append((Path(parts[0]), int(parts[1])))
    return hits


def baseline_context(pattern: str) -> tuple[str, str]:
    """Build the two baseline context blobs (whole-file, windowed) for a grep pattern.

    whole-file: grep output + full text of each distinct matched file (dedup'd).
    windowed:   grep output + a +/-WINDOW line slice around each match.
    """
    hits = _grep(pattern)
    grep_lines = [f"{f}:{ln}" for f, ln in hits]
    grep_blob = "\n".join(grep_lines)

    # whole-file variant: read each distinct matched file once
    seen_files: dict[Path, str] = {}
    for f, _ in hits:
        if f not in seen_files:
            try:
                seen_files[f] = f.read_text(errors="replace")
            except OSError:
                seen_files[f] = ""
    whole = grep_blob + "\n" + "\n".join(seen_files.values())

    # windowed variant: +/- WINDOW lines around each match
    file_lines: dict[Path, list[str]] = {}
    windows: list[str] = []
    for f, ln in hits:
        if f not in file_lines:
            try:
                file_lines[f] = f.read_text(errors="replace").splitlines()
            except OSError:
                file_lines[f] = []
        lines = file_lines[f]
        lo = max(0, ln - 1 - WINDOW)
        hi = min(len(lines), ln + WINDOW)
        windows.append("\n".join(lines[lo:hi]))
    windowed = grep_blob + "\n" + "\n".join(windows)

    return whole, windowed


# ── Tasks ───────────────────────────────────────────────────────────────────--

# Each task: a human question, the grep pattern an agent would start with for the
# baseline, and a thunk producing the Seam tool's JSON answer for the same question.


def build_tasks(conn) -> list[dict]:
    root = REPO_ROOT

    def j(obj) -> str:
        return json.dumps(obj, indent=2, default=str)

    return [
        {
            "question": "Who calls `upsert_file`? (find all callers/usages)",
            "grep": r"upsert_file",
            "seam_tool": "seam_context",
            "seam": lambda: j(handle_seam_context(conn, "upsert_file", root)),
        },
        {
            "question": "Blast radius of changing `init_db` (what breaks upstream)?",
            "grep": r"init_db",
            "seam_tool": "seam_impact",
            "seam": lambda: j(
                handle_seam_impact(conn, "init_db", root, direction="upstream")
            ),
        },
        {
            "question": "Where is FTS5 full-text search implemented?",
            "grep": r"fts5|FTS5|MATCH|bm25",
            "seam_tool": "seam_search",
            "seam": lambda: j(handle_seam_search(conn, "FTS5 full text search", root)),
        },
        {
            "question": "What are the functional areas / modules of this codebase?",
            # Baseline to grasp 'areas': skim every module's defs/classes.
            "grep": r"^(class |def |    def )",
            "seam_tool": "seam_clusters",
            "seam": lambda: j(handle_seam_clusters(conn, root)),
        },
        {
            "question": "How does the `init` CLI flow reach `upsert_file`? (call path)",
            "grep": r"index_one_file|upsert_file|walk_project",
            "seam_tool": "seam_trace",
            "seam": lambda: j(
                handle_seam_trace(conn, "index_one_file", "upsert_file", root)
            ),
        },
        {
            "question": "Understand `extract_edges`: its callers and callees.",
            "grep": r"extract_edges",
            "seam_tool": "seam_context",
            "seam": lambda: j(handle_seam_context(conn, "extract_edges", root)),
        },
    ]


# ── Run ─────────────────────────────────────────────────────────────────────--


def main() -> int:
    db_path = REPO_ROOT / ".seam" / "seam.db"
    if not db_path.exists():
        print("No index found. Run `seam init` first.", file=sys.stderr)
        return 1
    # init_db (idempotent) guarantees schema/migrations; then a read connection.
    init_db(db_path).close()
    conn = connect(db_path)

    tasks = build_tasks(conn)
    rows = []
    tot_whole = tot_win = tot_seam = 0

    for t in tasks:
        whole, windowed = baseline_context(t["grep"])
        seam_out = t["seam"]()
        tw, twin, ts = est_tokens(whole), est_tokens(windowed), est_tokens(seam_out)
        tot_whole += tw
        tot_win += twin
        tot_seam += ts
        rows.append(
            {
                "q": t["question"],
                "tool": t["seam_tool"],
                "whole": tw,
                "win": twin,
                "seam": ts,
                "red_whole": 100 * (1 - ts / tw) if tw else 0.0,
                "red_win": 100 * (1 - ts / twin) if twin else 0.0,
            }
        )

    conn.close()

    # ── Report (markdown to stdout) ──
    def red(a: int, b: int) -> str:
        return f"{100 * (1 - b / a):.1f}%" if a else "n/a"

    print("| # | Question | Tool | Baseline (whole-file) | Baseline (windowed) | Seam | Reduction vs whole | vs windowed |")
    print("|---|----------|------|----------------------:|--------------------:|-----:|-------------------:|------------:|")
    for i, r in enumerate(rows, 1):
        print(
            f"| {i} | {r['q']} | `{r['tool']}` | {r['whole']:,} | {r['win']:,} | "
            f"{r['seam']:,} | {r['red_whole']:.1f}% | {r['red_win']:.1f}% |"
        )
    print(
        f"| | **TOTAL** | | **{tot_whole:,}** | **{tot_win:,}** | **{tot_seam:,}** | "
        f"**{red(tot_whole, tot_seam)}** | **{red(tot_win, tot_seam)}** |"
    )
    print(f"\nEstimated tokens (chars/{CHARS_PER_TOKEN}). Window = +/-{WINDOW} lines. "
          f"Source scope: {SEARCH_DIR.relative_to(REPO_ROOT)}/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
