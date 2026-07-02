# Security Policy

## Supported versions

Seam is pre-1.0 and ships fixes on the latest release line only.

| Version | Supported |
| ------- | --------- |
| 0.3.x   | ✅        |
| < 0.3   | ❌        |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Use GitHub's private vulnerability reporting:

1. Go to the [Security tab](https://github.com/Catafal/seam/security/advisories) of the repo.
2. Click **"Report a vulnerability"**.
3. Describe the issue, affected versions, and a reproduction if you have one.

You'll get an acknowledgement within **5 business days**, and we'll keep you updated as we
investigate and prepare a fix. Once resolved, we'll publish an advisory and credit you
(unless you prefer to remain anonymous).

## Scope notes

Seam is designed to be **local-first and offline**:

- It runs no network calls and uses no API keys at runtime (verified at the syscall level by `.github/workflows/no-egress.yml`, Linux CI).
- It stores its index in a local SQLite database under `.seam/`.
- The MCP server (`seam start`) and the Explorer web server (`seam serve`, bound to
  `127.0.0.1`) are intended for local use only — do not expose them to untrusted networks.
- The npm shim (`@catafal/seam`, P5.1) introduces no new network path in Seam itself — it
  delegates to `uvx` (bundled with uv), which downloads `seam-code` from PyPI over TLS. The
  npm package executes no install-time code (`no postinstall` script); it only runs when
  explicitly invoked via `npx`. The supply-chain trust model is: npm registry → shim sources
  (auditable, no binary blobs); PyPI → seam-code wheel (covered by Trusted Publishing + PEP 740
  attestations on the PyPI release).

The most relevant threat surfaces are therefore: parsing untrusted source files
(tree-sitter), the optional web UI, and the install/config writers (`seam install`).
Reports in these areas are especially welcome.

## Release integrity

PyPI releases are built and published exclusively via CI on a `v*` tag push
(`release.yml`). Supply-chain controls in place:

- Every GitHub Actions `uses:` ref is pinned to a full 40-hex commit SHA
  (enforced by `tests/unit/test_actions_pin_audit.py` in `make gate`).
- The publish job requires `gate` (ruff + mypy + pytest) and a `smoke` matrix
  (3.12, 3.13) to pass first — a red build cannot reach PyPI.
- `checksums.txt` (SHA-256 of sdist + wheel) is attached to each GitHub Release.
- PEP 740 build attestations (`attestations: true`) are enabled on the PyPI
  Trusted Publishing (OIDC) publish step.
