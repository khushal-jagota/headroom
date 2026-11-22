/** Linux symlink checks cannot prove alias behavior on a case-insensitive filesystem. */
import fs from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { executor_paths, validate_server_url } from './configuration.ts';
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

test.skipIf(process.platform !== 'linux')(
  'Linux uses the join data root and refuses relative XDG paths',
  () => {
    const home = fs.realpathSync(fs.mkdtempSync(join(tmpdir(), 'linux-root-')));
    roots.push(home);
    const fallback = join(home, '.local/share/panels/executor-typescript/data');
    expect(executor_paths({ HOME: home }).root).toBe(fallback);
    expect(executor_paths({ HOME: home, XDG_DATA_HOME: '' }).root).toBe(fallback);
    const xdg = join(home, 'data');
    expect(executor_paths({ HOME: home, XDG_DATA_HOME: xdg }).root).toBe(
      join(xdg, 'panels/executor-typescript/data'),
    );
    const explicit = join(home, 'private-executor');
    expect(
      executor_paths({ HOME: home, XDG_DATA_HOME: xdg, PLAN_EXECUTOR_ROOT: explicit }).root,
    ).toBe(explicit);
    expect(() => executor_paths({ HOME: home, XDG_DATA_HOME: 'relative' })).toThrow('absolute');
    expect(() =>
      executor_paths({ HOME: home, XDG_DATA_HOME: 'relative', PLAN_EXECUTOR_ROOT: explicit }),
    ).toThrow('absolute');
  },
);

// The launch, join and transport all take this one value, so the one check gates them all.
test('the home address is an HTTPS origin or an absolute socket path, nothing else', () => {
  const socket = '/home/owner/Panels/data/executor-link/server.sock';
  expect(validate_server_url(socket)).toBe(socket);
  expect(validate_server_url('/Users/owner/Library/Application Support/PanelsHome/s.sock')).toBe(
    '/Users/owner/Library/Application Support/PanelsHome/s.sock',
  );
  expect(validate_server_url('https://home.example.ts.net/')).toBe('https://home.example.ts.net');
  for (const refused of [
    'http://127.0.0.1:8767',
    'relative/server.sock',
    '/data/../executor-link/server.sock',
    '/data/executor-link/',
    '/data/executor-link/server.sock\n',
  ])
    expect(() => validate_server_url(refused), refused).toThrow();
});
