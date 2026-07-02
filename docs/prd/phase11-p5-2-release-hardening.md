# Phase 11 · P5.2 — Release Hardening

> Roadmap item **#13** ("staged carefully"). Completes the trust story P5.3/P5.4 started:
> those proved the **runtime** is trustworthy (no rogue writes, no egress); P5.2 proves the
> **supply chain** is — verifiable, gated, reproducible releases.
>
> **Mixed validation surface.** The pin-audit checker + its gate test are fully locally
> testable on macOS (they run inside `make gate`). The `release.yml` changes (checksums,
> GitHub Release attach, smoke-install, attestations) are CI-only — proven on a real tag/
> release run.

## Problem Statement

Seam ships as the PyPI distribution `seam-code`, published from a tag via Trusted
Publishing. But the release path has no trust guarantees to match Seam's local-first
promise. A user who `pip install seam-code` today has: no `checksums.txt` to verify the
download against; no proof the published wheel actually installs or that the `seam` console
command runs; and no assurance the release was even gated — `ci.yml` does not trigger on
tags, and the release build job runs only `uv build`, so a red build can be published. Every
GitHub Action in the release path is pinned to a **mutable** ref (`@v6`, `@v7`, a
`@release/v1` branch), so a compromised or retagged action could silently alter a release.
The "never publish a red build" comment in the workflow is currently aspirational, not
enforced. For a tool whose entire pitch is trust, the distribution channel is the weakest
link.

## Solution

Harden the release so every published artifact is gated, verified, checksummed, and built
from SHA-pinned actions — and add a machine check that keeps the actions pinned.

- A pure **action-pin auditor** scans every workflow's `uses:` refs and fails if any is
  mutable (not a full commit SHA / not a local action). It is unit-tested and wired into the
  normal gate, so a mutable ref cannot land in a PR — this check runs locally on macOS, no CI
  round-trip needed.
- The release workflow gains: the **full gate run before publish** (never publish red), a
  **pre-publish smoke-install** that installs the built wheel across the supported Python
  matrix and runs `seam --version` (a broken artifact fails the release instead of shipping),
  a generated **`checksums.txt`** (sha256 of sdist + wheel) **attached to a GitHub Release**
  alongside the dists, and **PEP 740 attestations** enabled where practical.
- All GitHub Actions are **pinned to full commit SHAs** (with a `# vX` comment so Dependabot
  keeps them current).

After this lands, a release is only publishable if it is green, installable, checksummed,
and built from pinned actions — and CI proves the actions stay pinned on every change.

## User Stories

1. As a Seam maintainer, I want the full gate (lint + typecheck + test) to run before any PyPI publish, so that a red build can never be released.
2. As a Seam maintainer, I want the published wheel installed and smoke-tested (`seam --version`) before publish, so that a broken artifact fails the release instead of reaching users.
3. As a maintainer, I want the wheel smoke-installed across the supported Python matrix (3.12, 3.13), so that a version-specific packaging break is caught pre-release.
4. As an adopter, I want a `checksums.txt` published with each release, so that I can verify the integrity of the sdist and wheel I download.
5. As an adopter, I want the built distributions attached to a GitHub Release (not only ephemeral Actions artifacts), so that I can download and verify them independently of PyPI.
6. As a security-conscious adopter, I want the release built with PEP 740 provenance/attestations where practical, so that I can trace an artifact back to the workflow that produced it.
7. As a maintainer, I want every GitHub Action pinned to a full commit SHA, so that a retagged or compromised action cannot silently alter a build.
8. As a maintainer, I want a machine check that fails CI (and the local gate) if any workflow uses a mutable action ref, so that pinning cannot silently regress.
9. As a maintainer, I want the pin-audit check to run inside `make gate`, so that a mutable ref is caught locally before I even push — no CI round-trip.
10. As a maintainer, I want each SHA pin annotated with a `# vX` comment, so that Dependabot can propose readable version bumps and I can review them.
11. As a maintainer, I want the pin auditor to treat local `./`-path actions as allowed, so that first-party composite actions are not false-flagged.
12. As a maintainer, I want the pin auditor to be a pure, unit-tested module with synthetic fixtures, so that its classification logic is trustworthy and provable without a live workflow.
13. As a maintainer, I want the auditor invocable as `python -m tests.support.actions_pin_audit <workflow>...` returning a nonzero exit on any mutable ref, so that CI wiring is a thin wrapper with no embedded parsing.
14. As a maintainer, I want the release job to still use Trusted Publishing (OIDC, no long-lived token), so that the hardening does not reintroduce a stored secret.
15. As a maintainer, I want the smoke-install to gate the publish job (publish `needs` smoke), so that ordering makes an unverified publish structurally impossible.
16. As a maintainer, I want a clear failure message naming the offending workflow + ref when the pin audit fails, so that I can fix it without decoding YAML.
17. As a contributor, I want the pinning policy documented, so that when I add a new workflow step I know to pin it (and can run the auditor locally to confirm).
18. As a maintainer, I want the checksums generated deterministically from the exact artifacts that get published, so that the published `checksums.txt` provably matches the uploaded files.
19. As a maintainer, I want the release smoke-install to install from the built wheel *file* (not from PyPI by name), so that verification happens before publish and cannot depend on a package that isn't published yet.
20. As a maintainer, I want no change to the product runtime or the `make gate` semantics beyond the added pin-audit check, so that hardening the release does not perturb the read path or existing tests.

## Implementation Decisions

- **Two-part slice mirroring P5.3/P5.4 (deep testable module + CI hardening).** The pinning invariant is enforced by a pure, locally-gate-run checker; the release-workflow hardening is YAML proven on a real release. This keeps the CI-only-validated surface minimal.

- **New deep module — the action-pin auditor (`tests/support/actions_pin_audit.py`).** Lives with `fs_audit.py` / `egress_audit.py` in `tests/support/`. Stdlib-only, pure, never raises on malformed input:
  - A classifier for a single `uses:` ref: PINNED if it is `owner/repo@<40-hex-sha>` or a local `./…` action; MUTABLE otherwise (`@v1`, `@main`, `@release/v1`, any non-SHA ref). A trailing `# vX` comment is ignored for classification.
  - A scanner: given workflow file text (or a set of files), return the list of MUTABLE offender refs (with the workflow name / ref for the message). Empty == clean.
  - A thin `main(argv)` CLI: scan the named workflow files, print each offender with a clear prefix, return `0` (clean) or `1` (≥1 mutable ref). Invocable as `python -m tests.support.actions_pin_audit <workflow>...`.
  - Regex/line-based scan of `uses:` lines — no YAML dependency needed (mirrors `egress_audit`'s line-based approach); a full SHA is validated as exactly 40 hex chars.

- **Gate wiring (this is the key difference from P5.4).** A pytest test in the suite scans the *real* `.github/workflows/*.yml` and asserts zero mutable refs, so the invariant is enforced by `make gate` locally AND by `ci.yml` (which mirrors the gate). Optionally the CI also runs the `python -m` form as an explicit step, but the gate test is the primary enforcement.

- **Pin all GitHub Actions to full commit SHAs (confirmed direction).** Convert `actions/checkout@v6`, `astral-sh/setup-uv@v7`, `actions/upload-artifact@v7`, `actions/download-artifact@v8`, and `pypa/gh-action-pypi-publish@release/v1` to `@<sha>  # vX` across `ci.yml`, `release.yml`, and `no-egress.yml`. Dependabot (already configured for the github-actions ecosystem) keeps them current via the version comment. The pin auditor then passes.

- **`release.yml` hardening:**
  - **Gate before publish:** add a gate job (lint + typecheck + test, reusing the `make gate` steps / the CI recipe) that the build/publish chain `needs`, so a tag never publishes a red build. The pin-audit test runs as part of that gate.
  - **Pre-publish smoke-install:** a job (matrixed over the supported Python versions) that installs the built wheel *file* from the build artifact into a clean environment and runs `seam --version`; the `publish` job `needs` it. Installing the file (not `pip install seam-code` by name) means verification happens before publish and does not depend on an as-yet-unpublished package.
  - **Checksums:** generate `checksums.txt` (sha256 of the exact sdist + wheel in `dist/`) in the build job and carry it through as an artifact.
  - **GitHub Release attachment:** attach the dists + `checksums.txt` to a GitHub Release for the tag (in addition to PyPI), so artifacts are independently downloadable/verifiable. This needs `contents: write` on that job only (least-privilege, scoped to the release-creation step).
  - **PEP 740 attestations:** enable attestations in the `pypa/gh-action-pypi-publish` step where practical (recent versions produce them by default with `id-token: write`); make it explicit.
  - **Trusted Publishing preserved:** OIDC, `environment: pypi`, `id-token: write` — no stored token introduced.

- **No product code change, no schema change, no new config knob, no new MCP tool.** MCP tool count stays 16. This is CI + test-support only.

## Testing Decisions

- **A good test here asserts external behavior — offending-ref-in → verdict-out — not internals.** The auditor is tested by feeding synthetic workflow text and asserting the returned offender list / `main` exit code. The gate test asserts a real, observable property of the repo (all workflow actions are SHA-pinned).

- **Module under test:** `tests/support/actions_pin_audit.py` — unit-tested in `tests/unit/test_actions_pin_audit.py`. Coverage: a SHA-pinned ref → clean; a `@v1` tag ref → offender; a `@main`/`@release/v1` branch ref → offender; a local `./action` → allowed; a `# vX` trailing comment ignored; a 39- or 41-char hex (not a real SHA) → offender; non-`uses:` lines ignored; `main` exit codes for clean vs dirty files. Plus the **repo-invariant gate test** that scans the real `.github/workflows/` and asserts zero offenders (this is what fails if someone adds a mutable ref).

- **Prior art in the codebase:**
  - `tests/support/egress_audit.py` + `tests/unit/test_egress_audit.py` (P5.4) — the exact "pure line-based classifier in `tests/support/`, synthetic fixtures, thin `python -m` CLI" pattern this repeats.
  - `tests/support/fs_audit.py` (P5.3) — the same deep-module discipline.
  - `.github/workflows/ci.yml` / `release.yml` / `no-egress.yml` — the workflow conventions (pinning target, `permissions`, structure) being hardened.

- **The release.yml hardening is validated by a real release run, not `make gate`.** The gate covers the pin auditor + the repo-invariant test; the checksums / smoke-install / GitHub Release attach / attestations are proven when a version tag is pushed and the release workflow runs green. This split is called out so no one expects local `make gate` to exercise the publish path.

- **Gate:** the new module + tests keep `make gate` green (ruff + mypy + full suite). Type-hinted (`X | None`), imports at top, snake_case, ≤200 lines/function, ≤1000 lines/file.

## Out of Scope

- **Actually cutting a v0.x release / publishing to PyPI** — this slice hardens the *mechanism*; pushing a release tag is a separate, deliberate maintainer action.
- **Sigstore/cosign signing beyond PEP 740 attestations** — the "where practical" provenance is the PyPI-native attestations; heavier signing infrastructure is a possible later follow-on.
- **SLSA build-level provenance attestation frameworks** — out of scope for this MVP; PEP 740 via Trusted Publishing is the pragmatic bar.
- **Reproducible-build bit-for-bit verification** — checksums prove integrity of the published artifact, not build reproducibility; the latter is a separate, larger effort.
- **Changing the build backend or packaging layout** (hatchling, force-include, extras) — untouched.
- **The `npm` shim (P5.1)** and **diagnostics/soak (P5.5)** — separate roadmap items.
- **Product runtime changes** — none; this is CI + test-support only.

## Further Notes

- This is a **trust-tier** slice: its value is a hardened, provable release path, not a runtime feature. It is the supply-chain complement to P5.3 (installer write-scope) and P5.4 (no-egress).
- Fail-closed spirit carries over: the pin auditor errs toward flagging (an ambiguous ref is treated as mutable), and the publish is structurally gated behind `needs: [gate, smoke]` so an unverified publish is impossible, not merely discouraged.
- After approval, split with `/to-issues`: S1 = the pin auditor + unit tests + repo-invariant gate test + converting all existing refs to SHA pins (fully locally verifiable); S2 = the `release.yml` hardening (checksums, GitHub Release attach, smoke-install matrix, gate-before-publish, attestations — CI-verified on a real release). Implement on a `codex/`-prefixed worktree.
- The maintainer cannot fully validate S2 without pushing a tag; plan to rely on a dry-run (e.g. a throwaway pre-release tag, or `workflow_dispatch` if added) or careful review as the acceptance signal for the publish path.
