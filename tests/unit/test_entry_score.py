"""P6b — framework entry-point scoring tests.

Covers:
  - compute_entry_score: pure scoring from file path pattern + decorator text.
  - The score is stored on symbols.entry_score at INDEX time (upsert_file).
  - list_entry_points() sorts by entry_score * reach (a low-reach framework
    route outranks a high-reach utility).
"""

import tempfile
from pathlib import Path

from seam.analysis.processes import compute_entry_score, list_entry_points
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol


def _sym(name: str, file: str, decorators: list[str] | None = None) -> Symbol:
    return Symbol(
        name=name,
        kind="function",
        file=file,
        start_line=1,
        end_line=2,
        docstring=None,
        signature=None,
        decorators=decorators,  # type: ignore[typeddict-item]
        is_exported=None,  # type: ignore[arg-type]
        visibility=None,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(source=source, target=target, kind="call", file=file, line=1, confidence="EXTRACTED")


# ── compute_entry_score (pure) ───────────────────────────────────────────────


def test_score_path_pattern_views() -> None:
    """A symbol in a Django-style views.py scores above a plain utility file."""
    views = compute_entry_score("/proj/app/views.py", None)
    util = compute_entry_score("/proj/app/utils.py", None)
    assert views > util
    assert util == 1.0  # no pattern, no decorator → neutral baseline


def test_score_path_pattern_routes_dir() -> None:
    """A file under a routes/ directory is boosted."""
    assert compute_entry_score("/proj/src/routes/orders.py", None) > 1.0


def test_score_decorator_route() -> None:
    """A @app.route decorator boosts a symbol even in a neutral file path."""
    assert compute_entry_score("/proj/handlers.py", ["@app.route('/x')"]) > 1.0


def test_score_decorator_router() -> None:
    """A FastAPI @router.get decorator is detected."""
    assert compute_entry_score("/proj/svc.py", ["@router.get('/items')"]) > 1.0


def test_score_never_raises_on_bad_input() -> None:
    """Bad input (None path) returns the neutral baseline, never raises."""
    assert compute_entry_score(None, None) == 1.0
    assert compute_entry_score("/x.py", "not-a-list") == 1.0  # type: ignore[arg-type]


# ── stored at index time + read-path sort ────────────────────────────────────


def test_entry_score_stored_on_symbols() -> None:
    """upsert_file computes and persists entry_score per symbol."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "s.db"
        conn = init_db(db)
        fp = Path(tmp) / "views.py"
        fp.write_text("# stub\n")
        f = str(fp)
        upsert_file(conn, Path(f), "python", "h", [_sym("index_view", f)], [])
        row = conn.execute(
            "SELECT entry_score FROM symbols WHERE name='index_view'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] is not None
        assert row[0] > 1.0  # views.py path pattern boosts it


def test_framework_route_outranks_higher_reach_utility() -> None:
    """A low-reach view in views.py outranks a higher-reach utility root.

    This is the measurable goal: ranking by entry_score * reach, not raw reach.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "s.db"
        conn = init_db(db)

        (root / "views.py").write_text("# stub\n")
        (root / "helpers.py").write_text("# stub\n")
        views_f = str(root / "views.py")
        util_f = str(root / "helpers.py")

        # The view has reach 2 (calls a 2-deep chain).
        view_syms = [_sym(n, views_f) for n in ("show_page", "render_one", "render_two")]
        view_edges = [
            _edge("show_page", "render_one", views_f),
            _edge("render_one", "render_two", views_f),
        ]
        upsert_file(conn, Path(views_f), "python", "h1", view_syms, view_edges)

        # The utility root has reach 3 (calls a 3-deep chain) but lives in a plain file.
        util_syms = [_sym(n, util_f) for n in ("big_util", "u1", "u2", "u3")]
        util_edges = [
            _edge("big_util", "u1", util_f),
            _edge("u1", "u2", util_f),
            _edge("u2", "u3", util_f),
        ]
        upsert_file(conn, Path(util_f), "python", "h2", util_syms, util_edges)

        points = list_entry_points(conn, repo_root=root)
        names = [p["name"] for p in points]
        # Raw reach would put big_util (reach 3) above show_page (reach 1).
        # entry_score * reach must flip that: the view ranks first.
        assert names.index("show_page") < names.index("big_util")
        conn.close()
