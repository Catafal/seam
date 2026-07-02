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

The most relevant threat surfaces are therefore: parsing untrusted source files
(tree-sitter), the optional web UI, and the install/config writers (`seam install`).
Reports in these areas are especially welcome.
