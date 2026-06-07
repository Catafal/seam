"""Relevance ranking + self-reference classification for seam_impact output (E2/E3).

LEAF MODULE — pure functions over plain dicts/strings. Imports only stdlib.
No database access, no config, never raises. Mirrors the leaf discipline of
seam/query/names.py and seam/analysis/builtins.py so the ranking rules can be
unit-tested exhaustively without fixtures.

WHY this module exists (the usability gap it closes):
  When seam_impact analyses a CLASS, expand_impact_seeds fans the seed out to
  every member, so the upstream walk surfaces the class's OWN sibling methods
  (Foo.a "depends on" Foo.b) as direct dependents. Alphabetical ordering floats
  these self-references above the EXTERNAL callers an agent actually cares about,
  and under the per-tier cap the externals fall off the bottom. The 2026-06-07
  neutral re-benchmark confirmed this empirically: recall improved (more real
  dependents found) but usability did not, because the right answer was below the
  cut line. This module ranks external dependents ahead of self-references BEFORE
  the cap, so the cap drops self-refs first — exactly as the handler already drops
  test dependents before production ones.

Definitions:
  container       — the class/struct a symbol belongs to. Derived from a qualified
                    name's first segment ("Foo.bar" -> "Foo"; "pkg.Foo.bar" -> "pkg").
  self-reference  — an entry that belongs to the target's own container (the target
                    class itself, or any of its members). These are the entries an
                    agent is already editing when they change the target.

Conservatism contract (carried from the rest of the codebase):
  When classification is uncertain, treat the entry as EXTERNAL. Never hide a real
  external dependent by mis-flagging it self-ref. Never raise on the read path.
"""

from typing import Any


def owning_container(name: str) -> str | None:
    """Return the container segment of a qualified symbol name, or None if bare.

    The container is everything before the LAST dot — consistent with bare_name()
    in seam/query/names.py, which takes everything after the last dot.

    Examples:
        "Foo.bar"      -> "Foo"
        "pkg.Foo.bar"  -> "pkg.Foo"
        "bar"          -> None        (no dot — a bare name has no container)
        ""             -> None        (empty)
        ".bar"         -> None        (leading dot — empty container is not a container)
        "Foo."         -> "Foo"       (trailing dot — degenerate but container is "Foo")

    WHY everything-before-last-dot (not first segment):
      Symbols are stored as "Container.member". For a deeply-qualified name like
      "pkg.Foo.bar" the member is "bar" and its container is "pkg.Foo" — matching
      how get_member_names builds "Class." prefixes. Using the first segment would
      misclassify members of a dotted-package container.

    Never raises.
    """
    if not name or "." not in name:
        return None
    prefix, _, _ = name.rpartition(".")
    # A leading-dot name (".bar") has an empty prefix → not a real container.
    return prefix or None


def classify_self_ref(
    entry_name: str,
    container: str | None,
    self_names: set[str],
) -> bool:
    """Return True when an impact entry belongs to the target's own container.

    An entry is a self-reference when EITHER:
      1. Its name is in self_names (the container itself or a bare member name), OR
      2. Its owning container equals the target container (catches every qualified
         member form — "Foo.bar" — regardless of whether Tier B inference qualified
         the edge target).

    The two checks are complementary: the owning_container check handles qualified
    entries ("Foo.bar"), while self_names handles the container name itself ("Foo")
    and BARE member entries ("bar", whose owning_container is None).

    Args:
        entry_name:  the dependent symbol's name (bare or qualified).
        container:   the target's container, or None when the target is a free
                     function / bare name with no container. None -> always False
                     (a target with no container can have no self-references).
        self_names:  {container} ∪ {bare member names}. Bare members must be listed
                     explicitly because owning_container("bar") is None.

    Never raises. Returns False on the safe (external) side when container is None.
    """
    if not container:
        return False
    if entry_name in self_names:
        return True
    return owning_container(entry_name) == container


def relevance_key(
    entry: dict[str, Any],
    container: str | None,
    self_names: set[str],
) -> tuple[bool, bool]:
    """Sort key ordering external-production entries first, self-references last.

    Returns (is_self_ref, is_test). Python sorts False < True, so the ascending
    order is:
        (False, False) — external production   ← kept first under the cap
        (False, True)  — external test
        (True,  False) — self-ref production
        (True,  True)  — self-ref test          ← dropped first under the cap

    This SUPERSEDES the prior single-key production-before-test sort by adding
    self-reference as the PRIMARY key while keeping is_test as the secondary key.
    Used with a STABLE sort, so the analysis layer's ascending-distance/alphabetical
    order is preserved within each group and results stay deterministic.
    """
    is_self = classify_self_ref(entry.get("name", ""), container, self_names)
    is_test = bool(entry.get("is_test", False))
    return (is_self, is_test)


def order_by_relevance(
    entries: list[dict[str, Any]],
    container: str | None,
    self_names: set[str],
) -> list[dict[str, Any]]:
    """Return entries stably re-ordered external-first, self-references last.

    Stable: within the external and self-reference groups the input order
    (the analysis layer's ascending-distance/alphabetical order) is preserved,
    so the closest external dependents survive an entries[:limit] cap.
    Never mutates the input list.
    """
    return sorted(entries, key=lambda e: relevance_key(e, container, self_names))


def partition_self_refs(
    entries: list[dict[str, Any]],
    container: str | None,
    self_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split entries into (external, self_refs), preserving input order in each.

    Used by the "hide" self-ref mode: externals are kept in the output, self_refs
    are dropped and only their COUNT is surfaced (mirrors the hidden_tests
    mechanism). Never mutates the input list.
    """
    external: list[dict[str, Any]] = []
    self_refs: list[dict[str, Any]] = []
    for entry in entries:
        if classify_self_ref(entry.get("name", ""), container, self_names):
            self_refs.append(entry)
        else:
            external.append(entry)
    return external, self_refs
