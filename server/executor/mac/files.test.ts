/** Real filesystem checks protect races that a value-only Python comparison cannot prove. */
import {
  closeSync,
  constants,
  fstatSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, test } from 'vitest';
import {
  copy_verified_at,
  directory_descriptor,
  file_digest,
  file_version,
  install_verified_at,
  regular_descriptor,
  verified_version,
} from './files.ts';
import { flock_descriptor, unlink_relative } from './platform.ts';

const roots: string[] = [];
function scratch(): string {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-files-')));
  roots.push(root);
  return root;
}
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe('retained executor file capabilities', () => {
  test('ancestor replacement cannot redirect copy, failed-copy cleanup, install, or deletion', () => {
    const root = scratch(),
      owned = join(root, 'owned'),
      held = join(root, 'held');
    const foreign = join(root, 'foreign'),
      source = join(root, 'source');
    mkdirSync(owned);
    mkdirSync(foreign);
    writeFileSync(source, Buffer.from('immutable provider bytes\0🦉'));
    writeFileSync(join(foreign, 'sentinel'), 'do not alter');
    writeFileSync(join(foreign, 'target'), 'foreign target');
    const before = readdirSync(foreign)
      .sort()
      .map((name) => [name, readFileSync(join(foreign, name)).toString('hex')]);
    const [sha, count] = file_digest(source);
    const parent = directory_descriptor(owned);
    try {
      renameSync(owned, held);
      symlinkSync(foreign, owned, 'dir');
      copy_verified_at(source, parent, 'copy', sha, count);
      expect(() => copy_verified_at(source, parent, 'bad', '0'.repeat(64), count)).toThrow(
        'incomplete or corrupt file',
      );
      install_verified_at(source, parent, 'target', sha, count);
      unlink_relative(parent, 'copy');
      expect(() => install_verified_at(source, parent, '../escape', sha, count)).toThrow(
        'unsafe directory component',
      );
      expect(readdirSync(held)).toEqual(['target']);
      expect(readFileSync(join(held, 'target'))).toEqual(readFileSync(source));
      expect(
        readdirSync(foreign)
          .sort()
          .map((name) => [name, readFileSync(join(foreign, name)).toString('hex')]),
      ).toEqual(before);
      expect(() => regular_descriptor(join(owned, 'target'))).toThrow();
    } finally {
      closeSync(parent);
    }
  });

  test('regular-file intake refuses links and directories and versions retain nanosecond integers', () => {
    const root = scratch(),
      source = join(root, 'source');
    writeFileSync(source, 'first');
    symlinkSync(source, join(root, 'link'));
    expect(() => regular_descriptor(join(root, 'link'))).toThrow();
    expect(() => regular_descriptor(root)).toThrow('artifact is not regular file');
    expect(() => directory_descriptor(root + '/../escape', { create: true })).toThrow(
      'unsafe directory component',
    );
    const descriptor = regular_descriptor(source);
    try {
      if (process.platform === 'linux') {
        const flags = readFileSync(`/proc/self/fdinfo/${descriptor}`, 'utf8').match(
          /^flags:\s+([0-7]+)/m,
        )![1]!;
        expect(BigInt(`0o${flags}`) & 0o2000000n).not.toBe(0n);
      }
      const stats = fstatSync(descriptor, { bigint: true });
      expect(file_version(source)).toEqual([
        stats.dev,
        stats.ino,
        stats.size,
        stats.mtimeNs,
        stats.ctimeNs,
      ]);
      expect(stats.mtimeNs).toBeGreaterThan(BigInt(Number.MAX_SAFE_INTEGER));
    } finally {
      closeSync(descriptor);
    }
    const [sha, count] = file_digest(source);
    expect(verified_version(source, sha, count)).toEqual(file_version(source));
    writeFileSync(source, 'different bytes');
    expect(verified_version(source, sha, count)).toBeNull();
    expect(file_version(join(root, 'link'))).toBeNull();
  });

  test('spool ownership excludes a second open and releases on unlock', () => {
    const root = scratch(),
      lock = join(root, 'lock');
    const one = openSync(lock, constants.O_CREAT | constants.O_RDWR, 0o600);
    const two = openSync(lock, constants.O_RDWR);
    try {
      expect(flock_descriptor(one, 'try-exclusive')).toBe(true);
      expect(flock_descriptor(two, 'try-exclusive')).toBe(false);
      expect(flock_descriptor(one, 'unlock')).toBe(true);
      expect(flock_descriptor(two, 'try-exclusive')).toBe(true);
      flock_descriptor(two, 'unlock');
    } finally {
      closeSync(two);
      closeSync(one);
    }
  });

  test.skipIf(process.platform !== 'linux')(
    'failed ancestor and regular-file intake close every acquired descriptor',
    () => {
      const root = scratch(),
        directory = join(root, 'directory');
      mkdirSync(directory);
      symlinkSync(directory, join(root, 'link'));
      const before = readdirSync('/proc/self/fd').length;
      for (let index = 0; index < 20; index++) {
        expect(() => regular_descriptor(directory)).toThrow('artifact is not regular file');
        expect(() => directory_descriptor(join(root, 'link', 'child'))).toThrow();
      }
      expect(readdirSync('/proc/self/fd').length).toBe(before);
    },
  );
});
