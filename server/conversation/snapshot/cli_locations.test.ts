import { chmodSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { backend_executable_path } from './environment.ts';

function account() {
  const home = mkdtempSync(join(tmpdir(), 'cli-home-'));
  const install = (folder: string, name = 'claude') => {
    mkdirSync(join(home, folder), { recursive: true });
    const path = join(home, folder, name);
    writeFileSync(path, '#!/bin/sh\n');
    chmodSync(path, 0o755);
    return path;
  };
  return {
    home,
    install,
    find: () => backend_executable_path('claude', { HOME: home, PATH: '/nonexistent' }),
  };
}

test('finds a CLI that a version manager installed, preferring the newest nvm version', () => {
  const { install, find } = account();
  install('.nvm/versions/node/v20.9.0/bin');
  const newest = install('.nvm/versions/node/v22.11.0/bin');
  install('.nvm/versions/node/v9.0.0/bin');
  expect(find()).toBe(newest);
});

test('finds fnm, volta, bun and a prefix named in .npmrc', () => {
  const fnm = account();
  expect(fnm.install('.local/share/fnm/node-versions/v22.1.0/installation/bin')).toBe(fnm.find());
  const volta = account();
  expect(volta.install('.volta/bin')).toBe(volta.find());
  const bun = account();
  expect(bun.install('.bun/bin')).toBe(bun.find());
  const custom = account();
  writeFileSync(join(custom.home, '.npmrc'), `prefix=${join(custom.home, 'tools')}\n`);
  expect(custom.install('tools/bin')).toBe(custom.find());
});

test('PATH wins over the folder scan, and a missing CLI is still missing', () => {
  const { home, install, find } = account();
  expect(find()).toBeNull();
  install('.volta/bin');
  const on_path = install('bin');
  expect(backend_executable_path('claude', { HOME: home, PATH: join(home, 'bin') })).toBe(on_path);
});
