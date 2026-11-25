import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { it } from 'vitest';
import { require_snapshot_space } from './snapshot_space.ts';

it('charges the WAL and managed skills against the backup filesystem before stopping', async () => {
  const root = await mkdtemp(join(tmpdir(), 'snapshot-space-'));
  try {
    const database = join(root, 'planner.db'),
      backups = join(root, 'other-filesystem');
    const measure = async (path: string) => {
      assert.equal(path, backups);
      return { bavail: 16n, bsize: 4096n };
    };
    await writeFile(database, 'db');
    await require_snapshot_space(database, backups, measure);
    await mkdir(join(root, 'skills'));
    await writeFile(join(root, 'skills/managed'), Buffer.alloc(32000));
    await writeFile(database + '-wal', Buffer.alloc(32000));
    await assert.rejects(require_snapshot_space(database, backups, measure), /cannot fit/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
