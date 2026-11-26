/** The staged update snapshot's catch-up and late check, on plain SQLite files. */
import { createHash } from 'node:crypto';
import {
  mkdtempSync,
  openSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
  writeSync,
  closeSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import BetterSqlite3 from 'better-sqlite3';
import { afterEach, expect, test } from 'vitest';
import {
  create_database_backup,
  stage_database_copy,
  UpdateSnapshotCheckFailure,
  verify_update_snapshot,
} from './capture.ts';
import { is_verified_snapshot, verified_snapshots } from './evidence.ts';

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
function home() {
  const root = mkdtempSync(join(tmpdir(), 'panels-staged-snapshot-'));
  roots.push(root);
  const database = join(root, 'planner.db');
  const db = new BetterSqlite3(database);
  db.pragma('journal_mode = WAL');
  db.exec(
    'CREATE TABLE rows (id INTEGER PRIMARY KEY, body BLOB, tag TEXT); CREATE INDEX rows_tag ON rows(tag)',
  );
  db.prepare(
    'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 2000) ' +
      'INSERT INTO rows (body, tag) SELECT randomblob(500), hex(randomblob(8)) FROM n',
  ).run();
  db.close();
  return { database, backups: join(root, 'backups') };
}
const bytes = (path: string) => createHash('sha256').update(readFileSync(path)).digest('hex');

test('a staged copy larger than the stopped database shrinks to it', async () => {
  // A VACUUM or a freed tail after the staged check leaves a shorter file to match.
  const { database, backups } = home();
  const staged = await stage_database_copy(database, backups);
  const db = new BetterSqlite3(database);
  db.exec('DELETE FROM rows WHERE id > 200; VACUUM');
  db.close();
  expect(statSync(database).size).toBeLessThan(statSync(join(staged, 'database.sqlite')).size);
  const snapshot = await create_database_backup(database, backups, 'f'.repeat(40), {
    staged_copy: staged,
  });
  expect(bytes(join(snapshot, 'database.sqlite'))).toBe(bytes(database));
  expect(await is_verified_snapshot(snapshot)).toBe(true);
});

test('a damaged image fails the late check and leaves retention and restore', async () => {
  // Its sha256 matches, so only SQLite's check finds the damage. Then nothing may count it.
  const { database, backups } = home();
  const older = await create_database_backup(database, backups, 'f'.repeat(40));
  const snapshot = await create_database_backup(database, backups, 'f'.repeat(40), {
    staged_copy: await stage_database_copy(database, backups),
  });
  const image = join(snapshot, 'database.sqlite');
  const page = 4096,
    last = statSync(image).size / page - 1;
  const handle = openSync(image, 'r+');
  writeSync(handle, Buffer.alloc(page, 0xa5), 0, page, last * page);
  closeSync(handle);
  const metadata = JSON.parse(readFileSync(join(snapshot, 'metadata.json'), 'utf8'));
  writeFileSync(
    join(snapshot, 'metadata.json'),
    JSON.stringify({ ...metadata, sha256: bytes(image) }),
  );
  const timed: string[] = [],
    alarms: unknown[] = [];
  await expect(
    verify_update_snapshot(snapshot, backups, {
      database,
      timed: (part) => timed.push(part),
      integrity_alarm: async (...args) => {
        alarms.push(args);
      },
    }),
  ).rejects.toBeInstanceOf(UpdateSnapshotCheckFailure);
  expect(timed).toEqual(['late_check']);
  // The alarm reads the live database's devices and names the earlier snapshot to restore.
  expect(alarms).toEqual([[database, basename(older)]]);
  expect(JSON.parse(readFileSync(join(snapshot, 'metadata.json'), 'utf8')).verified).toBe(false);
  expect(await verified_snapshots(backups)).toEqual([older]);
});

test('a staged copy that fails its check alarms and leaves nothing behind', async () => {
  // The update ends before the stop, so the owner learns of the damage only from the alarm.
  const { database, backups } = home();
  const db = new BetterSqlite3(database);
  db.unsafeMode(true);
  db.pragma('writable_schema = ON');
  db.prepare(
    "UPDATE sqlite_schema SET sql = 'CREATE INDEX rows_tag ON rows(body)' WHERE name = 'rows_tag'",
  ).run();
  db.close();
  const alarms: string[] = [];
  await expect(
    stage_database_copy(database, backups, {
      integrity_alarm: async (source) => {
        alarms.push(source);
      },
    }),
  ).rejects.toThrow(/integrity check failed/);
  expect(alarms).toEqual([database]);
  expect(readdirSync(backups)).toEqual([]);
});
