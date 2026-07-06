# PRD - Phase 11 RFC: Preview-Only Installer Autodetect

> Status: ready-for-agent.
> Created: 2026-07-06.
> GitHub issue: https://github.com/Catafal/seam/issues/406.
> Tracker label: `ready-for-agent`.
> Source roadmap: `.claude/tasks/codememory-inspired-agent-answerability-roadmap.md`.
> Previous shipped slice: Narrow MCP auto-init for `seam start` / issue #400.

## What Is Next On The Roadmap

The next roadmap phase should be **Preview-Only Installer Autodetect**.

This is next because the ranked CodeMemory-inspired roadmap has already closed
the higher-leverage graph and trust gaps:

1. HTTP call extraction quality has shipped as a protocol-edge quality slice.
2. Docker Compose and Dockerfile infra evidence has shipped as the first infra
   graph slice.
3. Hybrid exact type-resolution has shipped a narrow exact-receiver slice.
4. Semantic discovery productization, graph artifacts, diagnostics, docs/spec
   grounding, change planning, answerability evals, and other core
   answerability work exist in the current tree.
5. Narrow MCP auto-init has shipped as issue #400 and PR #405.
6. Installer write-scope tests, explicit installer targets, CLI guidance, MCP
   opt-in, and `--print-config` preview already exist.

The remaining ranked item is not a new graph domain. It is adoption polish:
help a user or coding agent understand which supported agent integrations appear
to be available on this machine, and exactly what Seam would write, without
writing anything automatically.

This phase should not make Seam behave like a broad auto-writing installer. Seam
already has a strong installer trust model: explicit target selection, preview
mode, idempotent owned blocks, reversible uninstall, fake-home write-scope
tests, and MCP config behind `--with-mcp`. The next improvement should preserve
that discipline while reducing setup friction.

## Problem Statement

Seam's product goal is to help commercial coding agents navigate a local
codebase with less token spend and stronger evidence than broad grep/read loops.
The installer supports that goal indirectly: it places token-lean CLI guidance
and, when explicitly requested, MCP server config into agent/editor environments
so tools such as Claude Code, Codex, Cursor, VS Code Copilot, Gemini CLI, and
Zed know how to use Seam.

The current installer is safe but still requires the user or agent to know the
right target ahead of time:

1. `seam install` defaults to Claude guidance.
2. `--target all` previews or installs every registered target, not only the
   targets that are likely relevant to the current machine or repo.
3. `--print-config` writes nothing, but it is target-selection driven rather
   than detection driven.
4. A user who wants to evaluate Seam across agents must already know which
   targets exist, which locations each target supports, and which files would be
   touched.
5. A coding agent helping a user set up Seam cannot ask a compact machine-readable
   question such as "which Seam integrations appear installed, already
   configured, unsupported, or safe to preview?"

This creates avoidable friction and token spend. The agent has to inspect README
text, run help output, try target-specific previews, and reason from scattered
paths. For a human, `--target all --print-config` can be too much because it
shows configuration for tools they may not use. For automation, broad `all`
output does not distinguish "installed", "configured", "supported but not
detected", "project hint present", and "unsupported location".

The risk is the opposite failure: broad autodetect installers are dangerous if
they silently write to every global config they can find. Seam should not copy
that behavior. Any auto-detection feature must be preview-first, read-only by
default, explicit about uncertainty, and separate from installation. Detection
should answer "what is likely available and what would be written?", not "write
to every likely config."

The problem is therefore:

> Seam needs a compact, preview-only, machine-readable installer discovery
> surface that detects supported agent targets and reports exact would-write
> effects, while preserving explicit opt-in for all filesystem mutations.

## Solution

Add preview-only installer autodetect as a read-only discovery/reporting layer on
top of the existing installer targets.

The user-facing shape should be one of these equivalent CLI contracts, selected
by implementation taste:

1. `seam install --auto --print-config`
2. `seam install --detect --print-config`
3. `seam install --target auto --print-config`
4. A dedicated read-only command such as `seam install-plan`

The PRD recommends `seam install --auto --print-config` for continuity with the
roadmap wording, with one hard validation rule: `--auto` must be preview-only in
the first implementation. Running `seam install --auto` without `--print-config`
must fail closed with a clear error that tells the user to choose explicit
targets or add `--print-config`.

The feature should produce a structured install plan. The plan should include:

1. Registered installer targets.
2. Detection status per target.
3. Detection evidence per target.
4. Supported locations per target.
5. Selected preview location.
6. Guidance paths that would be written.
7. MCP config path that would be written when `--with-mcp` is also present.
8. Existing Seam-owned guidance/config status if present.
9. Existing foreign config status when detectable without parsing sensitive
   values.
10. Warnings and recommended next commands.

The human output should be compact and grouped by target. The JSON output should
be stable enough for coding agents to branch on status values rather than parse
prose.

Detection must be conservative and local:

1. Read only the current project root and the user's home config paths already
   known to the target implementations.
2. Use safe filesystem existence checks and parse only supported config files.
3. Never execute agent binaries.
4. Never search the whole home directory.
5. Never read or display secret values.
6. Never call the network.
7. Never mutate files.

The first implementation should not add new installer targets. It should make
the existing target set easier and safer to choose.

## User Stories

1. As an AI coding agent, I want to ask Seam which installer targets appear
   relevant, so that I can set up the right agent integration without reading the
   whole installer documentation.
2. As an AI coding agent, I want detection output in JSON, so that I can branch
   on stable target statuses instead of parsing help text.
3. As an AI coding agent, I want preview-only autodetect to write nothing, so
   that setup exploration is safe in a user's real checkout.
4. As an AI coding agent, I want `--auto` without preview to fail closed, so that
   I never accidentally write to multiple agent configs.
5. As an AI coding agent, I want each detected target to include evidence, so
   that I can distinguish "project file exists" from "user config exists" from
   "binary on PATH".
6. As an AI coding agent, I want each target to report supported MCP locations,
   so that I do not try `--location project` for a user-only target such as
   Codex.
7. As an AI coding agent, I want each target to report guidance preview paths, so
   that I know which repo files would receive Seam's CLI playbook.
8. As an AI coding agent, I want each target to report MCP preview paths only
   when `--with-mcp` is requested, so that guidance-only setup stays clearly
   separate from MCP configuration.
9. As an AI coding agent, I want preview output to mark unsupported locations as
   skipped, so that I can select a valid explicit command later.
10. As an AI coding agent, I want the plan to recommend explicit follow-up
    commands, so that I can tell the user "run this exact install command" rather
    than guessing.
11. As an AI coding agent, I want targets already containing Seam guidance to be
    marked as already configured, so that I avoid duplicate setup suggestions.
12. As an AI coding agent, I want targets already containing Seam MCP entries to
    be marked as already configured, so that I know install would be idempotent.
13. As an AI coding agent, I want corrupt or unparsable config files to be marked
    as blocked or warning, so that I do not recommend a write that would require
    backup behavior without user awareness.
14. As an AI coding agent, I want unknown target detection to be absent rather
    than guessed, so that Seam does not invent support for tools it cannot
    configure.
15. As an AI coding agent, I want `--target all --print-config` behavior to
    remain available, so that explicit all-target previews do not regress.
16. As a human developer, I want to run one safe command to see which agent
    integrations Seam can help configure, so that setup is easier.
17. As a human developer, I want auto-detect preview to show exact files and
    owned blocks, so that I can review the blast radius before any write.
18. As a human developer, I want auto-detect preview to avoid dumping full config
    files, so that private local settings stay private.
19. As a human developer, I want `--with-mcp` to remain explicit, so that Seam
    does not add a standing MCP server unless I ask for it.
20. As a human developer, I want user-scope config paths shown only as paths and
    safe status, so that local config contents are not exposed in logs.
21. As a human developer, I want project-scope config hints to be detected from
    files such as `AGENTS.md`, `CLAUDE.md`, `.cursor`, `.vscode`, `.gemini`,
    and `.zed`, so that the preview prioritizes likely tools.
22. As a human developer, I want binary detection to be optional and low trust, so
    that PATH quirks do not make Seam overconfident.
23. As a human developer, I want a target with no evidence to still be listed as
    supported but not detected, so that I know Seam can configure it if I select
    it explicitly.
24. As a human developer, I want the preview to explain that detection is not
    installation, so that I do not mistake the report for a completed setup.
25. As a human developer, I want exact explicit commands in the output, so that I
    can copy or ask an agent to run the selected install.
26. As a Seam maintainer, I want detection logic in a small deep module, so that
    filesystem probing, config inspection, status classification, and command
    recommendation can be tested without invoking the full CLI.
27. As a Seam maintainer, I want target definitions to remain the source of truth
    for supported locations and paths, so that detection does not duplicate
    installer path rules.
28. As a Seam maintainer, I want detection evidence to be allowlisted, so that new
    targets cannot accidentally leak arbitrary config content.
29. As a Seam maintainer, I want tests proving `--auto --print-config` mutates
    nothing, so that the installer write-scope contract remains enforceable.
30. As a Seam maintainer, I want tests proving `--auto` without `--print-config`
    exits non-zero and writes nothing, so that fail-closed behavior is locked in.
31. As a Seam maintainer, I want tests covering fake HOME, project config hints,
    installed guidance, installed MCP, corrupt configs, unsupported locations,
    and no-evidence targets, so that the planner handles realistic states.
32. As a Seam maintainer, I want human output and JSON output generated from the
    same plan object, so that CLI and agent contracts cannot drift.
33. As a Seam maintainer, I want the plan object to avoid storing absolute paths
    outside explicit preview fields, so that output remains useful but not noisy.
34. As a future installer-target implementer, I want an optional detection hook
    per target, so that adding a target also adds a preview/detection contract.
35. As a future product maintainer, I want this phase to avoid new graph/index
    behavior, so that adoption polish does not complicate Seam's dependency
    semantics.

## Implementation Decisions

- Treat this as an installer UX and trust phase, not a code-intelligence graph
  phase.
- Add preview-only autodetect over the existing target registry. Do not add new
  targets in this PRD.
- Preserve existing explicit installer behavior:
  - `seam install` defaults to Claude guidance.
  - `--target <name>` remains explicit.
  - `--target all` remains explicit.
  - `--print-config` writes nothing.
  - `--with-mcp` remains required for MCP config preview or writes.
- Introduce `--auto` as a selector that is valid only with `--print-config` in
  the first implementation. If a later PRD wants `--auto --apply`, it must add a
  separate consent model and tests.
- Define a deep installer planning module. Its public interface should accept
  the project root, selected location, whether MCP is included, the resolved Seam
  command, the target registry, and an optional home/root override from tests. It
  should return a pure data plan without writing files.
- Keep concrete target modules as the source of truth for config paths,
  supported locations, guidance preview paths, and renderers.
- Add a lightweight target detection contract. Each target can provide detection
  hints through a method or helper. The first pass can be registry-driven rather
  than polymorphic if the implementation remains small, but detection rules must
  be centralized and tested.
- Use stable target statuses. Recommended initial statuses:
  - `configured`: Seam guidance or MCP entry is already present for this target.
  - `detected`: project or user evidence suggests the target is relevant.
  - `supported`: Seam supports the target but found no local evidence.
  - `skipped`: target cannot preview the selected location or mode.
  - `blocked`: config exists but cannot be safely inspected.
- Use stable evidence kinds. Recommended initial kinds:
  - `project_guidance_file`
  - `project_mcp_config`
  - `user_mcp_config`
  - `seam_guidance_present`
  - `seam_mcp_present`
  - `agent_project_hint`
  - `agent_user_hint`
  - `binary_on_path`
  - `config_unparseable`
- Detection may check for known project hint files and directories:
  - Claude: `CLAUDE.md`, `.claude/`, `.mcp.json`
  - Cursor: `.cursor/`, `.cursor/rules/`, `.cursor/mcp.json`
  - Codex: `AGENTS.md`, `.codex/` when project support is later added, user
    config path for current MCP support
  - VS Code: `.vscode/`, `.github/copilot-instructions.md`
  - Gemini: `GEMINI.md`, `.gemini/`
  - Zed: `AGENTS.md`, `.zed/`
- User-level detection may check only the exact known user config path for each
  target. It must not recursively scan `HOME`.
- PATH detection may use allowlisted executable names only, and it should be a
  weak evidence signal. It must not execute the binary.
- Config inspection must parse only the supported target config format and
  inspect only the known Seam key path. It must not dump or summarize unrelated
  config values.
- A corrupt supported config should not be silently treated as absent. It should
  report `blocked` or `detected_with_warning`, with a warning that an explicit
  install would create a backup through existing installer behavior.
- The output should include `recommended_next_calls` or equivalent commands.
  Examples:
  - `seam install . --target claude`
  - `seam install . --target cursor --with-mcp --location project`
  - `seam install . --target codex --location user --with-mcp`
- The JSON response should include enough information for agents to rank
  suggestions:
  - target name
  - status
  - confidence or priority
  - evidence list
  - supported locations
  - selected location
  - guidance preview paths
  - MCP preview path/config when requested
  - would write boolean
  - blocked/warning reasons
  - recommended explicit command
- The human response should not print every full guidance body by default for
  auto-detect if that becomes too noisy. The implementation may choose a compact
  summary for `--auto --print-config` and keep full body preview for explicit
  targets. If so, JSON should still expose exact preview paths and optionally
  full preview contents behind an explicit existing behavior.
- Keep this CLI-only. Do not add MCP write or install tools. If MCP exposes
  installer capability in the future, it must be read-only planning only.
- Do not change graph schema, index schema, query behavior, architecture output,
  or Explorer.
- Do not auto-run `seam init`, `seam start`, or any agent.
- Do not create a global config registry or persistent installer state.

## Testing Decisions

- Good tests for this feature assert externally observable behavior: stdout JSON
  shape, exit codes, recommended commands, detected statuses, and filesystem
  snapshots before/after. They should not assert private helper call counts.
- Add focused unit tests for the deep installer planning module:
  - no evidence returns supported targets with no writes;
  - project hint files produce detected statuses;
  - existing Seam guidance produces configured status;
  - existing Seam MCP entries produce configured status;
  - corrupt JSON/TOML target config produces blocked or warning status;
  - unsupported selected location produces skipped status;
  - Codex user-only MCP is represented correctly;
  - VS Code project-only MCP is represented correctly;
  - shared AGENTS.md guidance for Codex and Zed does not duplicate evidence
    incorrectly;
  - path and evidence output is allowlisted.
- Add CLI integration tests:
  - `seam install <root> --auto --print-config --json` succeeds and writes
    nothing;
  - `seam install <root> --auto --print-config --with-mcp --json` succeeds and
    writes nothing;
  - `seam install <root> --auto` exits non-zero and writes nothing;
  - `seam install <root> --auto --with-mcp` exits non-zero and writes nothing;
  - human output is compact and includes target names, statuses, and explicit
    follow-up commands;
  - invalid combinations retain the existing JSON error envelope.
- Extend installer write-scope tests:
  - fake HOME plus fake project root;
  - snapshot before and after auto-detect preview;
  - assert no created, modified, or deleted files across both watched roots.
- Reuse prior art:
  - installer unit tests for target path and entry shape;
  - install CLI integration tests for print preview and explicit targets;
  - installer write-scope audit tests for zero mutation and canary protection;
  - filesystem audit helper for before/after snapshots.
- Include no-egress expectations:
  - auto-detect preview must not make network calls;
  - auto-detect preview must not execute agent binaries;
  - optional PATH checks use `shutil.which`-style lookup only.
- Run focused verification during implementation:
  - installer unit tests;
  - install CLI integration tests;
  - installer write-scope integration tests;
  - ruff and mypy for touched installer/CLI modules.

## Out of Scope

- Applying auto-detected installs.
- Adding `--auto --apply`.
- Writing to global configs without explicit target/location consent.
- Adding MCP write tools for install, uninstall, or auto-detect.
- Adding new agent targets.
- Detecting unsupported or unknown agents.
- Scanning arbitrary home directories.
- Executing agent binaries.
- Runtime probing of editors or agents.
- Network calls.
- Reading or printing secret values.
- Changing index schema, graph schema, graph extraction, query tools, impact,
  trace, context, architecture, Explorer, or Web graph behavior.
- Kubernetes, Helm, Terraform, cloud, or infra detection.
- Cross-repo workspace detection.
- Replacing the existing explicit installer with a broad setup wizard.

## Further Notes

- This phase is intentionally lower graph leverage than the earlier roadmap
  items. It is still useful because Seam's value depends on agents actually
  adopting the right invocation path with low setup cost.
- The product bar remains trust first. A safe preview that suggests one explicit
  command is better than an auto-installer that writes to every detected config.
- The output should be designed for commercial coding agents: compact JSON,
  stable statuses, precise file paths, explicit caveats, and recommended next
  calls.
- The implementation should keep detection confidence humble. "Supported but not
  detected" is a valid and useful answer.
- This PRD should be implemented after closing or updating stale tracker items
  that imply already-shipped docs/spec grounding work is still pending.
