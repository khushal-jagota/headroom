import {
  mkdtempSync,
  rmSync,
  writeFileSync,
  symlinkSync,
  unlinkSync,
  existsSync,
  renameSync,
  mkdirSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { once } from 'node:events';
import { expect, test } from 'vitest';
import {
  acquireLock,
  acquireRuntimeOwnership,
  canonical_database_path,
  runtime_authority_path,
  observe_existing_flock,
} from '../core/locks/index.ts';
// A fake lock cannot prove exclusion between processes. The two cases use private files
// and a Node child that takes the flock through the server's lock module, without a server import.
const locks = new URL('../core/locks/index.ts', import.meta.url).href;
const probe =
  'const { acquireLock } = await import(process.argv[1]); if (acquireLock(process.argv[2]) === null) process.exit(23);';
test('another process owner excludes Node until that owner exits', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'panels-lock-child-')),
    db = join(dir, 'db');
  writeFileSync(db, '');
  const child = spawn(
    process.execPath,
    [
      '--input-type=module',
      '-e',
      `const { acquireLock } = await import(process.argv[1]);
const lock = acquireLock(process.argv[2]);
if (lock === null) process.exit(23);
console.log('ready');
process.stdin.on('end', () => lock.close()).resume();`,
      locks,
      `${db}.conversation-owner.lock`,
    ],
    { stdio: ['pipe', 'pipe', 'pipe'] },
  );
  try {
    await once(child.stdout, 'data');
    expect(observe_existing_flock(`${db}.conversation-owner.lock`)).toBe('held');
    expect(() => acquireRuntimeOwnership(db)).toThrow('ConversationRuntimeAlreadyOwned');
    child.stdin.end();
    await once(child, 'exit');
    expect(observe_existing_flock(`${db}.conversation-owner.lock`)).toBe('free');
    const handle = acquireRuntimeOwnership(db);
    handle.close();
  } finally {
    if (child.exitCode === null) {
      child.stdin.end();
      child.kill();
      await once(child, 'exit');
    }
    rmSync(dir, { recursive: true, force: true });
  }
});
test('Node ownership excludes another process and dispatcher contention returns false', () => {
  const dir = mkdtempSync(join(tmpdir(), 'panels-lock-node-')),
    db = join(dir, 'db');
  writeFileSync(db, '');
  const handle = acquireRuntimeOwnership(db);
  try {
    if (['linux', 'darwin'].includes(process.platform)) {
      const absent = join(dir, 'absent', 'probe.lock');
      expect(observe_existing_flock(absent)).toBe('free');
      expect(existsSync(join(dir, 'absent'))).toBe(false);
      expect(observe_existing_flock(runtime_authority_path(canonical_database_path(db)))).toBe(
        'held',
      );
      expect(observe_existing_flock(`${db}.conversation-owner.lock`)).toBe('held');
    }
    // The authority survives removal of every pathname under the original instance.
    renameSync(dir, dir + '.moved');
    try {
      expect(() => acquireRuntimeOwnership(db)).toThrow('ConversationRuntimeAlreadyOwned');
      expect(existsSync(dir)).toBe(false);
    } finally {
      renameSync(dir + '.moved', dir);
    }
    expect(() =>
      execFileSync(
        process.execPath,
        ['--input-type=module', '-e', probe, locks, `${db}.conversation-owner.lock`],
        { stdio: 'pipe' },
      ),
    ).toThrow(expect.objectContaining({ status: 23 }));
    expect(acquireLock(`${db}.conversation-owner.lock`)).toBeNull();
    const alias = join(dir, 'db-alias');
    symlinkSync(db, alias);
    expect(() => acquireRuntimeOwnership(alias)).toThrow('ConversationRuntimeAlreadyOwned');
    mkdirSync(join(dir, 'nested', 'child'), { recursive: true });
    symlinkSync(join(dir, 'nested', 'child'), join(dir, 'directory-link'));
    expect(canonical_database_path(join(dir, 'directory-link') + '/../future.db')).toBe(
      join(dir, 'nested', 'future.db'),
    );
    // A shared nonstrict resolver must not hide filesystem faults in a lock identity.
    expect(() => canonical_database_path(join(db, 'child'))).toThrow(
      expect.objectContaining({ code: 'ENOTDIR' }),
    );
    symlinkSync('loop', join(dir, 'loop'));
    expect(() => canonical_database_path(join(dir, 'loop'))).toThrow('symbolic link loop');
    handle.close();
    unlinkSync(db);
    const missing = acquireRuntimeOwnership(alias);
    try {
      expect(() => acquireRuntimeOwnership(db)).toThrow('ConversationRuntimeAlreadyOwned');
    } finally {
      missing.close();
    }
    expect(() =>
      execFileSync(
        process.execPath,
        ['--input-type=module', '-e', probe, locks, `${db}.conversation-owner.lock`],
        { stdio: 'pipe' },
      ),
    ).not.toThrow();
  } finally {
    handle.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
