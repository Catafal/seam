# Seam — Code Intelligence

This project uses **Seam itself** for local code intelligence.

## Always Do

- Before exploring unfamiliar code, start with `uv run seam schema --json` to understand the current index capabilities.
- For concept discovery, use `uv run seam query "<concept>" --json` or `uv run seam graph-search --json` before falling back to broad text search.
- Before changing an existing symbol, run `uv run seam impact <symbol> --json` and inspect direct dependents.
- Before committing, run `uv run seam changes --json` to check changed-symbol risk.
- If Seam reports a stale index, run `uv run seam sync` or `uv run seam init` before relying on results.

## Common Commands

| Task | Command |
|------|---------|
| Index or re-index the repo | `uv run seam init` |
| Refresh incrementally | `uv run seam sync` |
| Inspect index/tool capabilities | `uv run seam schema --json` |
| Find code by concept | `uv run seam query "<concept>" --json` |
| Find symbols by graph shape | `uv run seam graph-search --json` |
| Inspect one symbol | `uv run seam context <symbol> --json` |
| Check blast radius | `uv run seam impact <symbol> --json` |
| Check pre-commit risk | `uv run seam changes --json` |
| Find impacted tests | `uv run seam affected --json` |

## Self-Check Before Finishing

Before completing a code modification task, verify:
1. Seam impact was checked for modified existing symbols when applicable.
2. `uv run seam changes --json` confirms the expected affected scope when the index is available.
3. Tests relevant to the changed surface were run.
4. Any stale-index warning was resolved with `uv run seam sync` or documented as a limitation.
