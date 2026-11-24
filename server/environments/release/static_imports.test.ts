/** The real Node parser and resolver prove package containment without an application host. */
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { run_release_command } from './process.ts';

test('static runtime imports resolve transitively inside the candidate with import conditions', async () => {
  const root = await mkdtemp(join(tmpdir(), 'static-runtime-imports-'));
  const owner = new ChildProcesses();
  const app = join(root, 'app');
  const put = async (name: string, source: string) => {
    const path = join(app, name);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, source);
  };
  try {
    await put('package.json', '{"type":"module"}');
    await put(
      'server/main.ts',
      `
      import './bridge.ts';
      import { type Inline } from '../shared/inline.ts';
      import type { Omitted } from '../type-only-does-not-exist.ts';
      export type { Omitted } from '../type-export-does-not-exist.ts';
      import 'proof-package';
      import '../data.json' with { type: 'json' };
      import '../addon.node';
      import '../commonjs/entry.js';
      throw new Error('the guard must not evaluate application code');
    `,
    );
    await put('server/bridge.ts', "export { policy } from '../shared/policy.ts';");
    await put('shared/inline.ts', 'export interface Inline {}');
    await put('shared/policy.ts', 'export const policy = 1;');
    await put('commonjs/package.json', '{"type":"commonjs"}');
    await put('commonjs/entry.js', 'return;');
    await put('data.json', '{}');
    await put('addon.node', 'static native leaf');
    await put(
      'node_modules/proof-package/package.json',
      JSON.stringify({
        type: 'module',
        exports: { import: './runtime.js', require: './wrong-condition.js' },
      }),
    );
    await put('node_modules/proof-package/runtime.js', "export * from './leaf.js';");
    await put('node_modules/proof-package/leaf.js', 'export const result = 1;');
    const source = `
      import assert from 'node:assert/strict';
      import { readFileSync, writeFileSync, unlinkSync, symlinkSync } from 'node:fs';
      import { join } from 'node:path';
      import { verify_static_runtime_imports } from ${JSON.stringify(new URL('./static_imports.ts', import.meta.url).href)};
      const root = process.argv[1];
      const verify = () => verify_static_runtime_imports(root, ['server/main.ts']);
      verify();
      for (const name of ['shared/policy.ts', 'shared/inline.ts', 'node_modules/proof-package/leaf.js']) {
        const path = join(root, name);
        const bytes = readFileSync(path);
        unlinkSync(path);
        assert.throws(verify, /Unresolved static runtime import/);
        writeFileSync(path, bytes);
      }
      const policy = join(root, 'shared/policy.ts');
      unlinkSync(policy);
      const outside = join(root, '../outside.ts');
      writeFileSync(outside, 'export const policy = 1;');
      symlinkSync(outside, policy);
      assert.throws(verify, /Unresolved static runtime import/);
      console.log('static import graph and containment passed');
    `;
    const result = await run_release_command(
      [
        process.execPath,
        '--experimental-vm-modules',
        '--experimental-import-meta-resolve',
        '--input-type=module',
        '-e',
        source,
        app,
      ],
      { timeout_seconds: 10 },
      { child_processes: owner, signal: new AbortController().signal, deadline: null },
    );
    expect(result.stdout).toContain('static import graph and containment passed');
  } finally {
    owner.seal();
    await owner.settled();
    await rm(root, { recursive: true, force: true });
  }
});
