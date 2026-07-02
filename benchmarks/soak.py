"""Seam soak harness (P5.5) — sustained mixed read load against an indexed repo.

WHAT THIS IS FOR
----------------
A repeatable way to exercise Seam's read path under sustained mixed load so an
operator can surface memory leaks, watcher regressions, and slow-query paths
BEFORE they reach a user — without any telemetry or network call. It drives a
configurable number of mixed seam_search / seam_context / seam_impact / seam_trace
requests against an already-indexed repo, then prints a human-readable summary.

Run it under diagnostics to also capture an NDJSON trace:

    SEAM_DIAGNOSTICS=1 python benchmarks/soak.py --iterations 200

The handlers are called through diagnostics.run_query — the exact wrapper the CLI
read commands use — so when SEAM_DIAGNOSTICS=1 every request is recorded to the
NDJSON file and the summary's RSS / open-FD / DB-size fields are populated from the
diagnostics resource sampler. With diagnostics OFF the load still runs and the
timing summary is still printed; the resource fields show "n/a".

NOT part of `make gate` (mirrors bench-semantic / no-egress — local/optional-CI only).
"""

import argparse
import sys
import time
from pathlib import Path

from seam.analysis.diagnostics import get_recorder, run_query
from seam.indexer.db import connect, init_db
from seam.server.tools import (
    handle_seam_context,
    handle_seam_impact,
    handle_seam_search,
    handle_seam_trace,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ITERATIONS = 100
# Duration (ms) at/above which a request is counted "slow" in the printed summary.
# Independent of SEAM_DIAGNOSTICS_SLOW_MS (which gates NDJSON slow_query lines).
_SLOW_MS = 100.0


def _discover_requests(conn, root: Path) -> list[tuple[str, object]]:
    """Build a rotation of realistic request thunks from the live index.

    Returns a list of (tool_name, thunk) pairs. Args are pulled from the index so
    the soak exercises real symbols/edges rather than hardcoded fixtures. Degrades
    gracefully: whatever the index yields is used; an empty index yields only the
    generic search request.
    """
    symbols = [r[0] for r in conn.execute("SELECT name FROM symbols LIMIT 40").fetchall()]
    edges = conn.execute(
        "SELECT source_name, target_name FROM edges WHERE source_name != target_name LIMIT 20"
    ).fetchall()

    requests: list[tuple[str, object]] = []
    # A generic keyword search is always valid, even on an empty index.
    requests.append(
        ("seam_search", lambda: handle_seam_search(conn, "index query search", root, limit=20))
    )
    for name in symbols[:20]:
        requests.append(
            ("seam_search", (lambda n=name: handle_seam_search(conn, n, root, limit=10)))
        )
        requests.append(
            ("seam_context", (lambda n=name: handle_seam_context(conn, n, root, verbose=True)))
        )
        requests.append(
            (
                "seam_impact",
                (lambda n=name: handle_seam_impact(conn, target=n, root=root, direction="upstream")),
            )
        )
    for src, tgt in edges:
        requests.append(
            (
                "seam_trace",
                (lambda s=src, t=tgt: handle_seam_trace(conn, source=s, target=t, root=root)),
            )
        )
    return requests


def _fmt_bytes(v: int | None) -> str:
    if v is None:
        return "n/a"
    return f"{v / (1024 * 1024):.1f} MB"


def run_soak(root: Path, db_dir: Path | None, iterations: int) -> int:
    """Run `iterations` mixed requests against the index at root; print a summary."""
    db_path = (db_dir or root) / ".seam" / "seam.db"
    if not db_path.exists():
        print(f"No index found at {db_path}. Run `seam init` first.", file=sys.stderr)
        return 1

    init_db(db_path).close()  # idempotent — guarantees schema/migrations
    conn = connect(db_path)

    recorder = get_recorder()  # enabled iff SEAM_DIAGNOSTICS=1
    start_metrics = recorder.sample_resources(str(db_path))

    requests = _discover_requests(conn, root)
    print(
        f"Soak: {iterations} iterations over {len(requests)} distinct requests "
        f"(diagnostics {'ON' if recorder.enabled else 'off'})",
        file=sys.stderr,
    )

    queries_run = 0
    slow_count = 0
    total_ms = 0.0
    peak_ms = 0.0
    for i in range(iterations):
        tool, thunk = requests[i % len(requests)]
        t0 = time.perf_counter()
        run_query(tool, thunk)  # records to NDJSON when diagnostics is on
        dt = (time.perf_counter() - t0) * 1000.0
        queries_run += 1
        total_ms += dt
        peak_ms = max(peak_ms, dt)
        if dt >= _SLOW_MS:
            slow_count += 1

    end_metrics = recorder.sample_resources(str(db_path))
    conn.close()

    start_fds = (start_metrics or {}).get("open_fds")
    end_fds = (end_metrics or {}).get("open_fds")
    fd_delta = (end_fds - start_fds) if (start_fds is not None and end_fds is not None) else None
    peak_rss = (end_metrics or {}).get("rss_bytes")
    db_size = (end_metrics or {}).get("db_size_bytes")

    print("\n── Soak summary ──────────────────────────────")
    print(f"queries run        : {queries_run}")
    print(f"slow (>= {_SLOW_MS:.0f} ms)  : {slow_count}")
    print(f"avg latency        : {total_ms / queries_run:.2f} ms" if queries_run else "avg: n/a")
    print(f"peak latency       : {peak_ms:.2f} ms")
    print(f"peak RSS           : {_fmt_bytes(peak_rss)}")
    print(f"open-FD delta      : {fd_delta if fd_delta is not None else 'n/a'}")
    print(f"DB size            : {_fmt_bytes(db_size)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seam soak harness (P5.5).")
    parser.add_argument("--iterations", type=int, default=_DEFAULT_ITERATIONS)
    parser.add_argument("--path", type=str, default=str(REPO_ROOT), help="Indexed repo root.")
    parser.add_argument("--db-dir", type=str, default="", help="Override DB directory.")
    args = parser.parse_args(argv)
    db_dir = Path(args.db_dir).resolve() if args.db_dir else None
    return run_soak(Path(args.path).resolve(), db_dir, max(1, args.iterations))


if __name__ == "__main__":
    raise SystemExit(main())
