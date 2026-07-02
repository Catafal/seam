#!/usr/bin/env node
/**
 * bin.js — thin harness for the @catafal/seam npm shim.
 *
 * WHY thin: all decision logic lives in lib/invocation.js (pure, unit-tested).
 * This file only wires up env/argv, spawns, and exits. Easy to audit for security.
 *
 * Invocation: `npx @catafal/seam <args>`  →  `uvx --from seam-code==<version> seam <args>`
 *
 * Safety properties:
 *   - NEVER uses shell:true — argv is always passed as an array
 *   - NEVER downloads binaries (uv owns download/checksum/cache against PyPI)
 *   - uv absence → explicit non-zero exit + install guidance (no auto-install)
 *   - No postinstall script (this file only runs when invoked explicitly)
 */

'use strict';

const { spawnSync } = require('child_process');
const { resolveRunner, buildUvxArgs } = require('./lib/invocation.js');

// Read own version from package.json (co-located, always in sync with the shim).
// WHY not hardcode: a version bump in package.json is the single source of truth;
// this prevents shim version and pinned PyPI version from drifting independently.
const { version } = require('./package.json');

// The user's arguments: everything after `node bin.js` (argv[0]=node, argv[1]=bin.js).
const userArgv = process.argv.slice(2);

// SEAM_NPM_FROM lets CI/developers override the --from spec (e.g. to a local wheel
// or a pre-release: SEAM_NPM_FROM='seam-code==0.5.0.dev1').
const fromSpecOverride = process.env.SEAM_NPM_FROM || '';

// Locate uvx. Passing process.env lets resolveRunner honor SEAM_NPM_UVX overrides.
// No opts.probe passed → uses the default which-based probe.
const runner = resolveRunner(process.env);

if (!runner) {
  // uvx is not on PATH and no override was set.
  // Print a single actionable line to stderr (not stdout — callers that capture
  // stdout should still see the error; stderr is the conventional error channel).
  process.stderr.write(
    'seam: uvx not found. Install uv first: https://docs.astral.sh/uv/getting-started/installation/\n'
  );
  process.exit(1);
}

// Build the argv array for uvx. Array args (never a shell string) guarantees that
// user-supplied arguments cannot inject shell metacharacters.
const uvxArgs = buildUvxArgs(userArgv, { version, fromSpecOverride });

// Spawn uvx with inherited stdio so the child's output flows directly to the
// terminal (same UX as running uvx directly). stdio:'inherit' is the simplest and
// most correct choice here — no buffering, no encoding issues.
const child = spawnSync(runner, uvxArgs, { stdio: 'inherit' });

if (child.error) {
  // Spawn failed before the process started (e.g. ENOENT for a bad SEAM_NPM_UVX path).
  process.stderr.write(`seam: failed to run uvx: ${child.error.message}\n`);
  process.exit(1);
}

// Propagate the child's exit code exactly. `status` is null on signal termination
// (e.g. SIGKILL). Treat that as 1 so CI/pipelines always see a non-zero exit on
// abnormal termination (null ?? 0 would silently succeed, which is wrong).
process.exit(child.status ?? 1);
