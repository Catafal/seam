/**
 * invocation.js — pure, dependency-free deep module for the @catafal/seam npm shim.
 *
 * WHY a separate pure module: bin.js wires I/O and spawning; this module contains
 * all the DECISION logic that can be tested without actually running uvx or hitting
 * the network. Pure functions = easy vitest isolation with no stubs for the runtime.
 *
 * Exports (CommonJS):
 *   resolveRunner(env, { probe })  → string | null
 *   buildUvxArgs(userArgv, { version, fromSpecOverride })  → string[]
 */

'use strict';

const { execFileSync } = require('child_process');

/**
 * Locate the uvx executable.
 *
 * WHY a sentinel (null) instead of throwing: the caller (bin.js) owns the UX
 * decision — it prints a helpful message and exits non-zero. This function
 * stays pure-ish (no throws, no output) so it is easy to unit-test.
 *
 * @param {NodeJS.ProcessEnv} env         - process.env (injected for testability)
 * @param {{ probe?: (cmd: string) => boolean }} [opts]
 *   probe  - optional locator function; defaults to a `which`-style existence check.
 *            injected so tests can control whether uvx is "found" without spawning.
 * @returns {string|null}  uvx command to spawn, or null when uvx cannot be located.
 */
function resolveRunner(env, opts) {
  const { probe = _defaultProbe } = opts || {};

  // Explicit override — highest priority. Useful in CI or when uvx lives at a
  // non-standard path (e.g. inside a venv or a container mount).
  const override = env && env.SEAM_NPM_UVX;
  if (override && override.trim()) {
    return override.trim();
  }

  // Probe for uvx on the default search path.
  if (probe('uvx')) {
    return 'uvx';
  }

  // uvx not found — caller decides what to do.
  return null;
}

/**
 * Default probe: check whether `cmd` resolves to an executable.
 * Uses execFileSync so we never pass user-controlled data to a shell.
 *
 * Returns false on any error (command not found, permission denied, etc.)
 * so the function is always safe to call and never throws.
 *
 * @param {string} cmd
 * @returns {boolean}
 */
function _defaultProbe(cmd) {
  // `which` exits 0 when found, non-zero when not found (macOS/Linux).
  // `where` is the Windows equivalent; use process.platform to select.
  // If neither is on PATH (very unusual), execFileSync throws ENOENT → caught → false.
  const locator = process.platform === 'win32' ? 'where' : 'which';
  try {
    execFileSync(locator, [cmd], { stdio: 'ignore' });
    return true;
  } catch (_) {
    return false;
  }
}

/**
 * Build the argv array to pass to uvx.
 *
 * WHY return an array (not a string): shell=false in spawnSync requires an array.
 * Keeping arg construction here (not in bin.js) lets us test the exact array shape
 * without spawning anything.
 *
 * @param {string[]} userArgv            - arguments the user passed (may be empty)
 * @param {{ version?: string, fromSpecOverride?: string }} [opts]
 *   version          - the npm package version; used to pin `seam-code==<version>`
 *   fromSpecOverride - when set, replaces the entire `--from` spec (e.g. `seam-code`)
 * @returns {string[]}  e.g. ['--from', 'seam-code==0.4.0', 'seam', '--version']
 */
function buildUvxArgs(userArgv, opts) {
  const { version, fromSpecOverride } = opts || {};

  // Determine the `--from` spec.
  // Priority: explicit fromSpecOverride > version-pinned default.
  // WHY pin by version: reproducibility — the same npm package always installs
  // the same PyPI version, no silent upgrades.
  let spec;
  if (fromSpecOverride && fromSpecOverride.trim()) {
    spec = fromSpecOverride.trim();
  } else if (version) {
    spec = `seam-code==${version}`;
  } else {
    // Fallback: unversioned. Should not happen in production (bin.js always
    // passes its own version), but guards against bad callers gracefully.
    spec = 'seam-code';
  }

  // ['--from', <spec>, 'seam', ...userArgs]
  // Arg order is load-bearing: uvx expects --from before the tool name.
  return ['--from', spec, 'seam', ...(userArgv || [])];
}

module.exports = { resolveRunner, buildUvxArgs };
