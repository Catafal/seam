"""LEAF: the single source of Seam's agent-facing CLI guidance + per-format renderers.

One guide body, rendered into each agent's cheapest native mechanism so the
formats can never drift:

  * Claude Code  → a project skill (`SKILL.md`): the description sits in the
    always-loaded skill listing; the body loads only when the skill is invoked.
  * Cursor       → an "Agent Requested" rule (`.mdc`): surfaced by its
    `description`, pulled in only when relevant (`alwaysApply: false`).
  * Codex        → an inline `AGENTS.md` block (Codex has no progressive
    mechanism, so the full guide is always-loaded there).

The thin CLAUDE.md hook is a separate, tiny always-loaded pointer that boosts
skill-discovery reliability (~70-85% → ~95%) for ~20 tokens.

Pure: string constants + string builders. No IO, no Seam deps, never raises.
"""

# Marker name for the shared-file blocks (AGENTS.md / CLAUDE.md). The
# markdownfile leaf turns this into `<!-- seam:start -->` … `<!-- seam:end -->`.
BLOCK_MARKER = "seam"

# Retrieval-tuned discovery hook. No apostrophes and no "colon-space" so it is a
# safe single-quoted YAML scalar in both the skill and the Cursor rule frontmatter.
_DESCRIPTION = (
    "Query the Seam code-intelligence index for this repo via the `seam` CLI "
    "instead of grep — find callers, blast radius, symbol definitions, and repo "
    "structure. Use when exploring or editing code in this repository."
)
_WHEN_TO_USE = (
    'Use for "who calls X", "what breaks if I change X", "where is X defined", '
    'or "map this repo".'
)

# The escalation ladder is the whole point: start cheap (--quiet names), escalate
# to --json --lean only for risk tiers, full --json only for provenance.
_GUIDE_BODY = """\
# Using Seam (code intelligence)

Seam has indexed this repo into `.seam/seam.db` — a symbol + call graph.
Query it instead of grepping: faster, and far cheaper in tokens.

## Keep the index fresh first
- No `.seam/seam.db`?        → `seam init`
- `seam status` says stale?  → `seam sync`

## Escalation ladder — spend the fewest tokens, escalate only when needed
1. Discover / orient   `seam search <text> --quiet` · `seam query <concept> --quiet`
                       `seam context <symbol> --quiet`        (names + locations only)
2. Change risk         `seam impact <symbol> --json --lean`   (risk tiers, no enrichment)
3. Full provenance     `seam impact <symbol> --json`          (confidence, resolved_by)

## When to reach for what
- Unfamiliar area        → `seam query "<concept>" --quiet`
- Before editing X       → `seam impact X --json --lean`   (d=1 = WILL_BREAK)
- Trace how A reaches B  → `seam trace A B`
- Before committing      → `seam changes`
- Map repo / entrypoints → `seam structure --quiet` · `seam flows`

Prefer Seam over grep/read for: "who calls X", "what breaks if I change X",
"where is X defined", "what's in this area".

## MCP alternative
Same answers as native MCP tools if you prefer tool-calling: `seam install --with-mcp`.
The CLI is the default because it costs fewer tokens."""

# Thin always-loaded pointer for CLAUDE.md (Claude does not read AGENTS.md).
_CLAUDE_HOOK = """\
## Seam — this repo is indexed for code intelligence
For structural questions (callers, blast radius, definitions, repo map) use the `seam`
CLI instead of grep — start with `seam <cmd> --quiet`. Full usage: the `seam` skill.
If `.seam/seam.db` is missing run `seam init`; if `seam status` is stale run `seam sync`."""


def render_skill() -> str:
    """Full `.claude/skills/seam/SKILL.md` — YAML frontmatter + the guide body."""
    return (
        "---\n"
        "name: seam\n"
        f"description: '{_DESCRIPTION}'\n"
        f"when_to_use: '{_WHEN_TO_USE}'\n"
        "---\n\n"
        f"{_GUIDE_BODY}\n"
    )


def render_cursor_rule() -> str:
    """Full `.cursor/rules/seam.mdc` — "Agent Requested" frontmatter + the guide body.

    `alwaysApply: false` + a `description` + empty `globs` is the doc-confirmed
    recipe for a description-surfaced (progressive) Cursor rule.
    """
    return (
        "---\n"
        f"description: '{_DESCRIPTION}'\n"
        "globs:\n"
        "alwaysApply: false\n"
        "---\n\n"
        f"{_GUIDE_BODY}\n"
    )


def render_codex_block() -> str:
    """Block content for Codex `AGENTS.md` — the full guide (no progressive option)."""
    return _GUIDE_BODY


def render_claude_hook() -> str:
    """Block content for the thin `CLAUDE.md` discovery pointer."""
    return _CLAUDE_HOOK
