/**
 * invocation.test.js — vitest unit tests for the pure invocation module.
 *
 * WHY vitest: consistent with the web/ test suite in this repo; fast ESM support.
 * WHY no network/spawn: all tests inject fakes through the probe param and env
 * injection so the suite runs completely offline.
 */

import { describe, it, expect } from 'vitest';
import { createRequire } from 'module';

// CommonJS module loaded via createRequire so vitest (ESM runner) can import it.
const require = createRequire(import.meta.url);
const { resolveRunner, buildUvxArgs } = require('./invocation.js');

// ---------------------------------------------------------------------------
// buildUvxArgs — arg array construction
// ---------------------------------------------------------------------------

describe('buildUvxArgs', () => {
  const VERSION = '0.4.0';

  it('produces the canonical shape: --from seam-code==<version> seam ...args', () => {
    const result = buildUvxArgs(['--version'], { version: VERSION });
    expect(result).toEqual(['--from', 'seam-code==0.4.0', 'seam', '--version']);
  });

  it('version pin uses the passed version (not a hardcoded literal)', () => {
    const result = buildUvxArgs([], { version: '1.2.3' });
    expect(result[1]).toBe('seam-code==1.2.3');
  });

  it('fromSpecOverride replaces the entire spec', () => {
    const result = buildUvxArgs(['start', '.'], {
      version: VERSION,
      fromSpecOverride: 'seam-code',
    });
    expect(result).toEqual(['--from', 'seam-code', 'seam', 'start', '.']);
  });

  it('empty argv produces exactly [--from, spec, seam] with no trailing args', () => {
    const result = buildUvxArgs([], { version: VERSION });
    expect(result).toEqual(['--from', 'seam-code==0.4.0', 'seam']);
  });

  it('undefined argv produces exactly [--from, spec, seam]', () => {
    const result = buildUvxArgs(undefined, { version: VERSION });
    expect(result).toEqual(['--from', 'seam-code==0.4.0', 'seam']);
  });

  it('preserves positional flag order: ["start", "."]', () => {
    const result = buildUvxArgs(['start', '.'], { version: VERSION });
    expect(result).toEqual(['--from', 'seam-code==0.4.0', 'seam', 'start', '.']);
  });

  it('preserves option flag order: ["--version"]', () => {
    const result = buildUvxArgs(['--version'], { version: VERSION });
    expect(result[3]).toBe('--version');
  });

  it('preserves multi-flag order: ["--json", "impact", "foo"]', () => {
    const result = buildUvxArgs(['--json', 'impact', 'foo'], { version: VERSION });
    expect(result).toEqual(['--from', 'seam-code==0.4.0', 'seam', '--json', 'impact', 'foo']);
  });

  it('whitespace-only fromSpecOverride is ignored (falls back to version pin)', () => {
    const result = buildUvxArgs([], { version: VERSION, fromSpecOverride: '   ' });
    expect(result[1]).toBe('seam-code==0.4.0');
  });

  it('no opts at all → spec is bare "seam-code" (graceful fallback)', () => {
    const result = buildUvxArgs([]);
    expect(result).toEqual(['--from', 'seam-code', 'seam']);
  });

  it('always puts --from as the first arg', () => {
    const result = buildUvxArgs(['x'], { version: VERSION });
    expect(result[0]).toBe('--from');
  });

  it('always puts "seam" at index 2 (tool name)', () => {
    const result = buildUvxArgs(['x'], { version: VERSION });
    expect(result[2]).toBe('seam');
  });
});

// ---------------------------------------------------------------------------
// resolveRunner — uvx location logic
// ---------------------------------------------------------------------------

describe('resolveRunner', () => {
  it('returns the SEAM_NPM_UVX value when env override is set', () => {
    const env = { SEAM_NPM_UVX: '/custom/uvx' };
    const result = resolveRunner(env, { probe: () => false });
    expect(result).toBe('/custom/uvx');
  });

  it('trims whitespace from SEAM_NPM_UVX override', () => {
    const env = { SEAM_NPM_UVX: '  /custom/uvx  ' };
    const result = resolveRunner(env, { probe: () => false });
    expect(result).toBe('/custom/uvx');
  });

  it('ignores SEAM_NPM_UVX when it is empty string (falls through to probe)', () => {
    const env = { SEAM_NPM_UVX: '' };
    const result = resolveRunner(env, { probe: () => true });
    expect(result).toBe('uvx');
  });

  it('ignores SEAM_NPM_UVX when it is whitespace-only', () => {
    const env = { SEAM_NPM_UVX: '   ' };
    const result = resolveRunner(env, { probe: () => true });
    expect(result).toBe('uvx');
  });

  it('returns "uvx" when the injected probe finds it', () => {
    const env = {};
    const result = resolveRunner(env, { probe: (cmd) => cmd === 'uvx' });
    expect(result).toBe('uvx');
  });

  it('returns null when the injected probe does not find uvx', () => {
    const env = {};
    const result = resolveRunner(env, { probe: () => false });
    expect(result).toBeNull();
  });

  it('returns null when env is empty and probe fails', () => {
    const result = resolveRunner({}, { probe: () => false });
    expect(result).toBeNull();
  });

  it('SEAM_NPM_UVX override takes priority over a successful probe', () => {
    const env = { SEAM_NPM_UVX: '/override/uvx' };
    const result = resolveRunner(env, { probe: () => true });
    expect(result).toBe('/override/uvx');
  });
});
