# @catafal/seam

Run the [Seam](https://github.com/Catafal/seam) local code-intelligence CLI via `npx` — no global install needed.

## Prerequisites

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first (the shim delegates to `uvx` which is bundled with uv):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Usage

```bash
# One-off via npx (no install required)
npx @catafal/seam --help
npx @catafal/seam init
npx @catafal/seam search "auth token"
npx @catafal/seam impact init_db --json
```

On first run, `uvx` downloads `seam-code` from PyPI into its own cache (~seconds). Subsequent runs reuse the cache and start instantly.

## How it works

`npx @catafal/seam <args>` translates to:

```
uvx --from 'seam-code==<version>' seam <args>
```

The npm package version mirrors the PyPI package version exactly — `@catafal/seam@0.4.0` always installs `seam-code==0.4.0`. No drift, no silent upgrades.

## Environment overrides

| Variable | Purpose |
|---|---|
| `SEAM_NPM_UVX` | Override the `uvx` binary path (default: `uvx` on PATH) |
| `SEAM_NPM_FROM` | Override the `--from` spec (default: `seam-code==<version>`) |

## Publishing

The npm shim is published manually from this directory, trailing the matching PyPI release:

```bash
# After seam-code==<version> is live on PyPI:
cd pkg/npm
npm publish --access public
```

Bump `package.json` `version` in lockstep with `pyproject.toml` `version`. The gate test `test_npm_package_version_matches_pyproject` in `tests/unit/test_smoke.py` fails if they drift.
