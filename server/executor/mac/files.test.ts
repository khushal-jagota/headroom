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
  readlinkSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, test } from 'vitest';
import { FilesService, file_version } from './files.ts';
import { ServerLifecycleError } from '../../lifecycle/contracts.ts';
import {
  close_native_descriptor,
  directory_open_flags,
  flock_descriptor,
  open_relative,
  unlink_relative,
} from './platform.ts';

const roots: string[] = [];
const services: FilesService[] = [];
const expected_failures = new WeakSet<FilesService>();
function scratch(): string {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-files-')));
  roots.push(root);
  return root;
}
function files(): FilesService {
  const service = new FilesService();
  services.push(service);
  return service;
}
afterEach(async () => {
  for (const service of services.splice(0)) {
    if (expected_failures.has(service)) {
      await expect(service.stop({ deadline: performance.now() / 1000 + 10 })).rejects.toMatchObject(
        { name: 'DataCloneError' },
      );
      await expect(service.settled()).rejects.toMatchObject({ name: 'DataCloneError' });
    } else {
      await service.stop({ deadline: performance.now() / 1000 + 10 });
      await service.settled();
    }
  }
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe('retained executor file capabilities', () => {
  test('ancestor replacement cannot redirect copy, failed-copy cleanup, install, or deletion', async () => {
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
    const service = files(),
      [sha, count] = await service.file_digest(source);
    const parent = await service.open_directory(owned);
    try {
      renameSync(owned, held);
      symlinkSync(foreign, owned, 'dir');
      await service.copy_verified_at(source, parent, 'copy', sha, count);
      expect(statSync(join(held, 'copy')).mode & 0o777).toBe(0o600);
      await expect(
        service.copy_verified_at(source, parent, 'bad', '0'.repeat(64), count),
      ).rejects.toThrow('incomplete or corrupt file');
      await service.install_verified_at(source, parent, 'target', sha, count);
      // The worker owns the opaque parent; a main-thread path cannot replace its inode.
      await expect(
        service.install_verified_at(source, parent, '../escape', sha, count),
      ).rejects.toThrow('unsafe directory component');
      expect(readdirSync(held).sort()).toEqual(['copy', 'target']);
      expect(readFileSync(join(held, 'target'))).toEqual(readFileSync(source));
      expect(
        readdirSync(foreign)
          .sort()
          .map((name) => [name, readFileSync(join(foreign, name)).toString('hex')]),
      ).toEqual(before);
      await expect(service.file_digest(join(owned, 'target'))).rejects.toThrow();
    } finally {
      await service.close_directory(parent);
    }
    // Exercise the deletion capability through a directly held test descriptor.
    const descriptor = openSync(held, directory_open_flags);
    try {
      unlink_relative(descriptor, 'copy');
    } finally {
      closeSync(descriptor);
    }
    expect(readdirSync(held)).toEqual(['target']);
  });

  test('regular intake refuses links and directories, creates CLOEXEC descriptors, and retains nanosecond versions', async () => {
    const root = scratch(),
      source = join(root, 'source'),
      service = files();
    writeFileSync(source, 'first');
    symlinkSync(source, join(root, 'link'));
    await expect(service.file_digest(join(root, 'link'))).rejects.toThrow();
    await expect(service.file_digest(root)).rejects.toThrow('artifact is not regular file');
    await expect(service.open_directory(root + '/../escape', { create: true })).rejects.toThrow(
      'unsafe directory component',
    );
    const parent = openSync(root, directory_open_flags);
    const descriptor = open_relative(parent, 'source', constants.O_RDONLY | constants.O_NOFOLLOW);
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
      close_native_descriptor(descriptor);
      closeSync(parent);
    }
    const [sha, count] = await service.file_digest(source);
    expect(await service.verified_version(source, sha, count)).toEqual(file_version(source));
    writeFileSync(source, 'different bytes');
    expect(await service.verified_version(source, sha, count)).toBeNull();
    expect(file_version(join(root, 'link'))).toBeNull();
  });

  test('spool ownership excludes a second open and releases on unlock', () => {
    const root = scratch(),
      lock = join(root, 'lock');
    const one = openSync(lock, constants.O_CREAT | constants.O_RDWR, 0o600),
      two = openSync(lock, constants.O_RDWR);
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
    'failed intake closes all acquired worker descriptors',
    async () => {
      const root = scratch(),
        directory = join(root, 'directory'),
        service = files();
      mkdirSync(directory);
      symlinkSync(directory, join(root, 'link'));
      const retained = await service.open_directory(root);
      const before = readdirSync('/proc/self/fd').length;
      for (let index = 0; index < 20; index++) {
        await expect(service.file_digest(directory)).rejects.toThrow(
          'artifact is not regular file',
        );
        await expect(service.open_directory(join(root, 'link', 'child'))).rejects.toThrow();
      }
      expect(readdirSync('/proc/self/fd').length).toBe(before);
      await service.close_directory(retained);
    },
  );

  test('an expired shutdown deadline preserves admitted work and permits a later drain', async () => {
    const root = scratch(),
      source = join(root, 'source'),
      service = files();
    writeFileSync(source, Buffer.alloc(1024 * 1024, 7));
    // Both operations enter a cold worker before stop seals admission.
    const retained = service.open_directory(root);
    const admitted = service.file_digest(source);
    const stopped = service.stop({ deadline: performance.now() / 1000 - 1 });
    await expect(service.file_digest(source)).rejects.toThrow();
    await expect(stopped).rejects.toBeInstanceOf(ServerLifecycleError);
    await retained; // The service closes this deliberately unreturned handle at real settlement.
    expect((await admitted)[1]).toBe(1024 * 1024);
    await service.stop({ deadline: performance.now() / 1000 + 10 });
    await service.settled();
  });

  test.skipIf(process.platform !== 'linux')(
    'a failed request transport drains the worker and closes libc-owned handles',
    async () => {
      const root = scratch(),
        source = join(root, 'source'),
        service = files();
      writeFileSync(source, Buffer.alloc(1024 * 1024, 3));
      await service.open_directory(root);
      function descriptor_targets(): string[] {
        return readdirSync('/proc/self/fd').flatMap((name) => {
          try {
            return [readlinkSync(`/proc/self/fd/${name}`)];
          } catch (error) {
            if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
            throw error;
          }
        });
      }
      expect(descriptor_targets()).toContain(root);
      // Attach handlers before a later postMessage fails and rejects every pending request.
      const admitted = service.file_digest(source).then(
        () => null,
        (error: unknown) => error,
      );
      const broken = service.file_digest((() => {}) as unknown as string).then(
        () => null,
        (error: unknown) => error,
      );
      expect(await admitted).toMatchObject({ name: 'DataCloneError' });
      expect(await broken).toMatchObject({ name: 'DataCloneError' });
      expected_failures.add(service);
      await expect(service.stop({ deadline: performance.now() / 1000 + 10 })).rejects.toMatchObject(
        { name: 'DataCloneError' },
      );
      await expect(service.settled()).rejects.toMatchObject({ name: 'DataCloneError' });
      expect(descriptor_targets()).not.toContain(root);
    },
  );
});
