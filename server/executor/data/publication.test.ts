import { createHash } from 'node:crypto';
import { lstat, mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { expect, test } from 'vitest';
import { fixture, fixture_path } from '../../tests/ticket_fixture.ts';
import { FileStaging } from '../vps_files.ts';
import { PublicationGroup } from './publication.ts';
import { directory_descriptor, verified_version } from '../files.ts';
import type { WireValue } from '../contracts.ts';

// Paired source proof covers publication/receipts. Python has no hold links, so
// this unit alone proves pin custody and cleanup after a partially applied group.
test('holds retain overwritten inodes through a later conflict and both directories flush after cleanup', async () => {
  const database = fixture(),
    staging = new FileStaging(fixture_path(database));
  const manifest: WireValue<'FileManifest'> = {
    scope: 'ticket',
    owner_id: 'ticket',
    relative_path: 'result.txt',
    sha256: createHash('sha256').update('source').digest('hex'),
    byte_count: 6,
  };
  const second = { ...manifest, relative_path: 'second.txt' };
  const target = await staging.file_target(manifest),
    source = await staging.staged_file(manifest);
  const second_target = await staging.file_target(second),
    second_source = await staging.staged_file(second);
  await mkdir(dirname(target), { recursive: true });
  await mkdir(dirname(source), { recursive: true });
  for (const path of [target, second_target]) await writeFile(path, 'old');
  for (const path of [source, second_source]) await writeFile(path, 'source');
  const old = await lstat(target, { bigint: true });
  const target_parent = await directory_descriptor(dirname(target)),
    source_parent = await directory_descriptor(dirname(source));
  try {
    let flushes = 0;
    for (const parent of [target_parent, source_parent]) {
      const sync = parent.sync.bind(parent);
      parent.sync = async () => {
        flushes++;
        await sync();
      };
    }
    const entries = [];
    for (const [incoming, outgoing, description] of [
      [source, target, manifest],
      [second_source, second_target, second],
    ] as const)
      entries.push({
        source: { parent: source_parent, leaf: basename(incoming), path: incoming },
        target: { parent: target_parent, leaf: basename(outgoing), path: outgoing },
        version: (await verified_version(incoming, description))!,
      });
    const group = await PublicationGroup.prepare(entries);
    const holds = (await readdir(dirname(target))).filter((name) => name.startsWith('.hold-'));
    expect(holds).toHaveLength(2);
    for (const hold of holds)
      expect((await lstat(join(dirname(target), hold), { bigint: true })).nlink).toBe(2n);
    try {
      await writeFile(second_source, 'changed source');
      expect(() => database.write(() => group.publish())).toThrow('An artifact changed');
      // First rename survived the SQL rollback. Its old inode still has its hold.
      const pinned = await Promise.all(
        holds.map((name) => lstat(join(dirname(target), name), { bigint: true })),
      );
      expect(
        pinned.some(
          (status) => status.dev === old.dev && status.ino === old.ino && status.nlink === 1n,
        ),
      ).toBe(true);
    } finally {
      await group.close();
    }
    expect((await readdir(dirname(target))).filter((name) => name.startsWith('.hold-'))).toEqual(
      [],
    );
    expect(await readFile(target, 'utf8')).toBe('source');
    expect(await readFile(second_source, 'utf8')).toBe('changed source');
    expect(await readFile(second_target, 'utf8')).toBe('old');
    expect(flushes).toBe(2);
    expect(database.read((sql) => sql.all('SELECT * FROM mac_inbox_positions'))).toEqual([]);
  } finally {
    await target_parent.close();
    await source_parent.close();
  }
});
