# Phase 11 · P5.3 — Installer Write-Scope Audit

> Roadmap item **#6** of the "Proposed Phase Order" — *definitely add first* (trust tier).
> Test-only slice. No installer source change unless the audit surfaces a genuine
> sensitive-path leak.

## Problem Statement

Seam's installer (`seam install` / `seam uninstall`) is the one place the tool writes
**outside** the gitignored `.seam/` index — it creates agent-facing files in the repo
(`SKILL.md`, `.cursor/rules/seam.mdc`, marker blocks in `CLAUDE.md` / `AGENTS.md`) and,
with `--with-mcp`, in the user's home directory (`~/.claude.json`, `~/.cursor/mcp.json`,
`~/.codex/config.toml`). Those home-directory files are **shared** with the user's other
projects and tools.

Today a developer must *trust the README's word* that the installer only touches an
expected set of files, never writes outside the repo root or the user's agent-config
directories, leaves foreign content untouched, previews nothing when asked to preview,
and fully reverses itself on uninstall. The existing test suite asserts **positive**
facts ("`SKILL.md` exists", "foreign content preserved") but never proves the
**exhaustive negative**: that the set of files created/modified is *exactly* the expected
set and **nothing else on the filesystem was touched**. Installation is a trust boundary,
and that guarantee is currently unmeasured.

## Solution

Add a snapshot-based **write-scope audit** to the test suite that turns the installer's
safety guarantees into repeatable, measurable checks. The audit takes a full filesystem
snapshot of two isolated sandboxes (a fake repo root and a fake `HOME`) before and after
each installer operation, then asserts the exact set of created/modified/deleted paths
against an expected manifest — and, critically, that **every** changed path lives inside
one of the two sandboxes and nowhere else.

From the developer's perspective: after this slice lands, `make gate` proves — on every
run, across the full target × location × mode matrix — that the installer writes only
what it claims, preview mode is inert, uninstall is a clean reversal, and no unrelated
file (in the repo, in `HOME`, or anywhere else) is ever created, modified, or deleted.

## User Stories

1. As a Seam maintainer, I want an exhaustive filesystem diff around each installer run, so that I can prove the installer creates/modifies *only* its expected files and nothing else.
2. As a Seam maintainer, I want the audit to fail if any write lands outside the fake repo root or fake `HOME`, so that a regression that writes to a sensitive path is caught before merge.
3. As a developer evaluating Seam, I want the "installer only touches expected files" claim to be test-backed, so that I can trust `seam install` in my real environment.
4. As a developer, I want proof that `seam install` (guidance-only, the default) writes exactly the guidance artifacts for the selected target and nothing more, so that the default path is auditably minimal.
5. As a developer, I want proof that `seam install --with-mcp` writes exactly the guidance artifacts *plus* the one MCP config file for the selected target+location, so that the opt-in heavier path is also bounded.
6. As a developer, I want proof that `--print-config` (preview mode) produces **zero** filesystem mutations, so that I can safely inspect what would be written.
7. As a developer, I want proof that `seam uninstall` returns the filesystem to its pre-install state (modulo documented, benign residue), so that install/uninstall is a clean round-trip.
8. As a developer, I want proof that uninstall removes only Seam-owned files and Seam-owned marker blocks, never foreign files or foreign content, so that my hand-written `CLAUDE.md`/`AGENTS.md` prose survives.
9. As a Claude Code user, I want proof that user-scope MCP install touches only `projects.<my-repo>.mcpServers.seam` inside `~/.claude.json` and preserves every other project entry, so that installing Seam never disturbs my other MCP servers.
10. As a Cursor user, I want proof that user-scope MCP install writes only `~/.cursor/mcp.json` under my fake `HOME` and preserves any pre-existing `mcpServers`, so that my other Cursor servers are untouched.
11. As a Codex user, I want proof that user-scope MCP install edits only the `[mcp_servers.seam]` table in `~/.codex/config.toml` and preserves my other tables, comments, and formatting, so that my Codex config is not clobbered.
12. As a developer using `--target all`, I want proof that the aggregate write-set across claude+cursor+codex equals exactly the union of each target's expected files, so that the multi-target path has no surprise writes.
13. As a maintainer, I want proof that a corrupt pre-existing config produces exactly one additional expected `<path>.backup` file (and no other surprise), so that the corrupt-config safety net is itself audited rather than being an unexpected write.
14. As a maintainer, I want proof that no `*.tmp` temp files remain after any installer operation, so that the atomic-write mechanism never leaks partial files into the user's directories.
15. As a maintainer, I want the audit encoded as a reusable filesystem-snapshot helper, so that future installer targets can be added to the matrix with a one-line manifest rather than bespoke assertions.
16. As a maintainer, I want the snapshot helper itself to have unit tests, so that a bug in the audit harness cannot silently pass a broken installer.
17. As a developer, I want the audit to seed each sandbox with realistic foreign content (an existing `CLAUDE.md`, an `AGENTS.md`, an `~/.claude.json` with another project, a `~/.codex/config.toml` with another server) before installing, so that preservation is proven against real-world starting states, not empty dirs.
18. As a maintainer, I want the audit to run under the existing `tmp_path` + `monkeypatch HOME` isolation, so that it never touches the developer's real agent configs when the suite runs locally.
19. As a maintainer, I want re-running install (idempotent second run) to be audited as **zero** new mutations, so that the "no churn on re-install" guarantee is measured, not assumed.
20. As a maintainer, I want the documented, benign uninstall residue (an empty `mcpServers: {}` container left in `~/.claude.json`, and the file itself persisting) captured explicitly in the expected manifest, so that the audit distinguishes "acceptable residue" from "leak" deterministically.

## Implementation Decisions

- **Test-only slice.** The deliverable is new test code plus one new test-support module. Installer source in `seam/installer/*` and `seam/cli/install.py` is **not** modified. The audit *documents and locks in* current safe behavior. If — and only if — the audit surfaces a genuine sensitive-path leak (a write outside root/HOME, foreign content clobbered, or a real reversal failure), that finding is raised separately before any source change; it is out of scope to change installer behavior speculatively.

- **New deep module — filesystem snapshot/diff helper (`tests/support/fs_audit.py`).** A small, pure, installer-agnostic module extracted so it is unit-testable in isolation and reusable as the matrix grows:
  - `snapshot(roots)` walks the given directory roots and returns a mapping of each file (identified by a path relative to a common base) to a stable content digest. Directories with no files contribute nothing; unreadable files are represented deterministically rather than raising.
  - `diff(before, after)` returns a `FsChanges` value with three disjoint sets: `created`, `modified`, `deleted` (by path), computed by set arithmetic over the two snapshots.
  - The module imports only the stdlib (`hashlib`, `os`/`pathlib`). It has **no** knowledge of Seam or the installer — it is a generic filesystem-audit primitive. This is the "deep module, simple interface, rarely changes" the design calls for.
  - `tests/support/` is a **new** directory (the repo currently has no shared test-support package); it will contain an `__init__.py` so it imports cleanly. This is the single new convention this slice introduces.

- **New audit test module (`tests/integration/test_installer_write_scope.py`).** Consumes `fs_audit` and the real installer targets / CLI. Structure:
  - **Two isolated sandboxes per test:** `root = tmp_path / "repo"` (the project) and `home = tmp_path / "home"` (monkeypatched as `HOME`). Both are watched by the snapshot.
  - **Sensitive-path guard (subset + one canary):** every path in `created ∪ modified ∪ deleted` must resolve under `root` or under `home` — asserted as "changed-paths ⊆ (root ∪ home)"; any path outside both fails the test. Because `HOME` is monkeypatched to the fake sandbox, this simultaneously proves the real `~` is never touched. In addition, **one decoy ("canary") file is seeded in a sibling directory outside both sandboxes** (e.g. `tmp_path / "sibling" / "canary.txt`) and asserted byte-identical after every operation — a legible, concrete failure signal that complements the abstract subset check.
  - **Expected manifest per cell:** each matrix cell declares the exact expected `created` set (and, for foreign-seeded cells, the exact expected `modified` set). The audit asserts equality, not just membership.
  - **Matrix breadth (hybrid — the confirmed strategy):** the cheap, high-trust checks are run **exhaustively / parametrized** across target ∈ {claude, cursor, codex} × location ∈ {project, user} × mode ∈ {guidance-only (default), with-mcp} × operation ∈ {install, uninstall, print-config} — specifically the sensitive-path guard, preview inertness, uninstall round-trip, and the no-`.tmp`-leak check. The **deeper foreign-content-preservation** cells (which pre-seed realistic non-Seam content and assert byte-preservation) are run **representatively — one per target**, not per location, since user-scope targets exercise the same merge/preserve code path. This avoids ~20 near-identical deep cells while keeping the trust guarantees complete. Also covered: the `--target all` aggregate write-set, the idempotent-second-run (zero new mutations), and the codex + project + `--with-mcp` upfront-rejected cell (asserted to write nothing).

- **Expected write manifests (the source-of-truth encoded from the investigation):**
  - Guidance, claude → `.claude/skills/seam/SKILL.md`, `CLAUDE.md` (block).
  - Guidance, cursor → `.cursor/rules/seam.mdc`.
  - Guidance, codex → `AGENTS.md` (block).
  - MCP, claude/project → `.mcp.json`; claude/user → `~/.claude.json` (`projects.<abs-root>.mcpServers.seam`).
  - MCP, cursor/project → `.cursor/mcp.json`; cursor/user → `~/.cursor/mcp.json`.
  - MCP, codex/user → `~/.codex/config.toml` (`[mcp_servers.seam]`). Codex/project is rejected (no write).

- **Preview inertness:** the `--print-config` cells assert `diff(before, after)` is empty across both sandboxes — a stronger guarantee than the current "these two named files are absent" checks.

- **Uninstall round-trip:** snapshot pre-install → install → uninstall → snapshot-after. Assert the after-state equals the pre-install state **except** for the documented benign residue: in the user-scope Claude case, `~/.claude.json` persists with `mcpServers` reduced to `{}` (the owned leaf is removed; the empty parent container is not pruned). This residue is captured explicitly in the expected manifest so the audit treats it as *expected*, not as a leak. No installer change is made to prune empty containers (that would be scope creep).

- **Foreign-content preservation:** foreign-seeded cells pre-write realistic non-Seam content (a `CLAUDE.md`/`AGENTS.md` with the user's own prose, a `~/.claude.json` holding another project's servers, a `~/.codex/config.toml` with another server + comments). After install *and* after uninstall, those foreign fragments are asserted byte-preserved (for owned/marker files, the foreign portion; for JSON/TOML configs, the foreign keys/tables).

- **Temp-file leak check:** after each operation, assert no `*.tmp` files remain anywhere in the two sandboxes (the atomic-write path uses `tempfile.mkstemp` + `os.replace`; a leaked `.tmp` would indicate a broken write).

- **No new config knobs, no schema change, no migration, no new MCP tool.** MCP tool count stays 16.

## Testing Decisions

- **A good test here asserts external, observable filesystem state — never installer internals.** The audit compares before/after filesystem snapshots and inspected file *contents*; it does not assert on private functions, call counts, or module structure. This mirrors the note atop the existing `tests/unit/test_installer.py`: "All assertions are on external behavior (file contents / InstallResult), not internals."

- **Modules under test:**
  - `tests/support/fs_audit.py` — unit-tested directly: snapshot captures files under multiple roots; diff classifies created/modified/deleted correctly; empty dirs and nested paths behave; a modified file is detected by content change, not mtime. This guards the harness so it cannot pass a broken installer.
  - The installer end-to-end (via `seam.cli.main.app` with `CliRunner`, and via the concrete `ClaudeTarget`/`CursorTarget`/`CodexTarget` where a lower-level assertion is clearer) — audited through `fs_audit`.

- **Prior art in the codebase:**
  - `tests/integration/test_install_cli.py` — the existing `CliRunner` + `tmp_path` + `monkeypatch HOME` pattern this slice extends into an exhaustive-diff audit.
  - `tests/unit/test_installer.py` — the isolation discipline (never touch the developer's real `~/.claude.json`/`~/.cursor/mcp.json`/`~/.codex/config.toml`) and the "external behavior only" assertion style.

- **Isolation is mandatory and load-bearing:** every audit test monkeypatches `HOME` to the fake sandbox before any installer call, so a bug that writes to real `HOME` is caught (it lands outside the watched sandboxes → sensitive-path guard fails) rather than silently mutating the developer's machine.

- **Gate:** `make gate` (ruff + mypy + full test suite) must pass. New code is type-hinted (`X | None`, not `Optional[X]`), imports at top of file, snake_case, ≤200 lines/function, ≤1000 lines/file.

## Out of Scope

- **P5.4 no-egress proof** — the `strace -e connect` Linux CI job is a separate roadmap item (#7) and a separate slice.
- **Changing installer behavior** — including pruning the empty `mcpServers: {}` container left in `~/.claude.json` after uninstall. This slice audits and documents current behavior; behavior changes are only triggered if the audit finds a *genuine* leak, and would be scoped separately.
- **Windows path semantics** — the dev/CI environment is POSIX (darwin); the audit uses POSIX path assumptions. Cross-platform path auditing is not in scope.
- **The atomic-write internals** (`tempfile.mkstemp` / `os.replace` mechanics) — audited only via the observable "no `.tmp` leak" outcome, not by asserting the mechanism.
- **Auditing the `.seam/` index writes** — `seam init` and the index are already gitignored and covered elsewhere; this slice is exclusively about the installer's *out-of-`.seam/`* writes.
- **New agent targets** — the matrix covers the three shipped targets (claude, cursor, codex); adding targets is future work (the harness is designed so a new target is a one-line manifest addition).

## Further Notes

- This is a **trust-tier** slice: its value is a repeatable proof, not a feature. It should read as "the installer's promises are now enforced by the gate."
- The investigation that produced the write-target map and the existing-coverage gap analysis is recorded in the session that generated this PRD; the manifests above are the distilled source of truth.
- After this PRD is approved, split it into implementation slices with `/to-issues` (the project's established PRD → GitHub-issues workflow, per the handoff), then implement with `/tdd` on a `codex/`-prefixed branch/worktree.
- If the audit *does* surface a real leak or an unacceptable reversal residue, capture it as a separate finding/issue — do not silently expand this slice into an installer refactor.
