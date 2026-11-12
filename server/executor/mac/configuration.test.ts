/** Linux symlink checks cannot prove alias behavior on a case-insensitive filesystem. */
import fs from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { executor_paths } from './configuration.ts';
import { python_executor_root } from './platform.ts';

const roots: string[] = [];
afterEach(() => {
  vi.restoreAllMocks();
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});
test('physical Python-root aliases refuse through absent tails without folding distinct paths', () => {
  const home = fs.realpathSync(fs.mkdtempSync(join(tmpdir(), 'mac-root-alias-')));
  roots.push(home);
  const legacy = python_executor_root(home),
    alias = join(dirname(legacy), 'EXECUTOR');
  fs.mkdirSync(legacy, { recursive: true });
  fs.mkdirSync(alias, { recursive: true });
  const native_stat = fs.statSync,
    identity = native_stat(legacy, { bigint: true });
  if (native_stat(alias, { bigint: true }).ino !== identity.ino)
    expect(executor_paths({ HOME: home, PLAN_EXECUTOR_ROOT: alias }).root).toBe(alias);
  // Model the native lookup result, not a blanket lowercase path comparison.
  const observed: string[] = [];
  const lookup = vi
    .spyOn(fs, 'statSync')
    .mockImplementation((...args: Parameters<typeof fs.statSync>) => {
      observed.push(String(args[0]));
      return args[0] === alias ? identity : Reflect.apply(native_stat, fs, args);
    });
  const refusal = 'TypeScript executor root must be separate from the Python executor root';
  expect(() => executor_paths({ HOME: home, PLAN_EXECUTOR_ROOT: alias })).toThrow(refusal);
  const missing = join(alias, 'not-created', 'spool');
  expect(() => executor_paths({ HOME: home, PLAN_EXECUTOR_ROOT: missing })).toThrow(refusal);
  expect(observed).toContain(missing);
  expect(observed).toContain(alias);
  const denied = Object.assign(new Error('metadata denied'), { code: 'EACCES' });
  lookup.mockImplementation(() => {
    throw denied;
  });
  expect(() => executor_paths({ HOME: home, PLAN_EXECUTOR_ROOT: alias })).toThrow(denied);
});
