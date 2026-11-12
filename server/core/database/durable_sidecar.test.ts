/** The auxiliary opener accepts version zero and changes vacuum policy. It cannot prove
 * a frozen spool's refusal-before-mutation or its lossless nanosecond integer reads.
 */
import { afterEach, expect, test } from 'vitest';
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { assertDatabaseVersion, createDurableSidecarDatabase } from './index.ts';
import type { Database, DurableSidecarDatabaseOptions } from './contracts.ts';

const cleanup: (() => void)[] = [];
afterEach(() => {
  for (const close of cleanup.splice(0).reverse()) close();
});
function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'panels-durable-sidecar-'));
  cleanup.push(() => rmSync(root, { recursive: true, force: true }));
  return { root, path: join(root, 'spool.sqlite3') };
}
function open(path: string, options: Partial<DurableSidecarDatabaseOptions> = {}): Database {
  const database = createDurableSidecarDatabase(path, {
    schemaVersion: 5,
    foreignKeys: true,
    refusalMessage: 'unsupported spool format',
    initialize(tx) {
      tx.run('CREATE TABLE file_versions(modified_ns INTEGER NOT NULL)');
    },
    ...options,
  });
  cleanup.push(() => database.close());
  return database;
}

test('an existing version-zero file refuses before schema, WAL, or file bytes change', () => {
  const { root, path } = fixture();
  expect(assertDatabaseVersion(path, 5, 'unsupported spool format')).toBe(false);
  expect(readdirSync(root)).toEqual([]);
  writeFileSync(path, '');
  const before = readFileSync(path);
  let initialized = false;
  expect(() => assertDatabaseVersion(path, 5, 'unsupported spool format')).toThrow(
    'unsupported spool format',
  );
  expect(() =>
    open(path, {
      initialize() {
        initialized = true;
      },
    }),
  ).toThrow('unsupported spool format');
  expect(initialized).toBe(false);
  expect(readFileSync(path)).toEqual(before);
  expect(readdirSync(root)).toEqual(['spool.sqlite3']);
});

test('the durable profile preserves exact settings and int64 metadata across reopen', () => {
  const { path } = fixture();
  const database = open(path);
  expect(
    database.write((tx) =>
      tx.get(`SELECT
    (SELECT user_version FROM pragma_user_version) AS version,
    (SELECT journal_mode FROM pragma_journal_mode) AS journal,
    (SELECT synchronous FROM pragma_synchronous) AS synchronous,
    (SELECT fullfsync FROM pragma_fullfsync) AS fullfsync,
    (SELECT trusted_schema FROM pragma_trusted_schema) AS trusted_schema,
    (SELECT foreign_keys FROM pragma_foreign_keys) AS foreign_keys,
    (SELECT recursive_triggers FROM pragma_recursive_triggers) AS recursive_triggers,
    (SELECT auto_vacuum FROM pragma_auto_vacuum) AS vacuum`),
    ),
  ).toEqual({
    version: 5n,
    journal: 'wal',
    synchronous: 2n,
    fullfsync: 1n,
    trusted_schema: 0n,
    foreign_keys: 1n,
    recursive_triggers: 0n,
    vacuum: 0n,
  });
  const modified_ns = 1_735_689_600_123_456_789n;
  expect(
    database.write((tx) => tx.run('INSERT INTO file_versions VALUES (?)', [modified_ns]))
      .lastInsertRowid,
  ).toBe(1n);
  expect(database.write((tx) => tx.get('SELECT modified_ns FROM file_versions'))).toEqual({
    modified_ns,
  });
  expect(database.read((sql) => sql.get('SELECT modified_ns FROM file_versions'))).toEqual({
    modified_ns,
  });
  database.close();
  const reopened = open(path, {
    foreignKeys: false,
    initialize() {
      throw new Error('must not reinitialize');
    },
  });
  expect(reopened.read((sql) => sql.all('SELECT modified_ns FROM file_versions'))).toEqual([
    { modified_ns },
  ]);
  expect(
    reopened.read((sql) => sql.get('SELECT trusted_schema FROM pragma_trusted_schema')),
  ).toEqual({ trusted_schema: 0n });
  expect(reopened.write((sql) => sql.get('SELECT foreign_keys FROM pragma_foreign_keys'))).toEqual({
    foreign_keys: 0n,
  });
  expect(reopened.read((sql) => sql.get('SELECT foreign_keys FROM pragma_foreign_keys'))).toEqual({
    foreign_keys: 0n,
  });
});

test('a failed fresh initializer rolls back its schema and leaves version zero incompatible', () => {
  const { path } = fixture();
  expect(() =>
    open(path, {
      initialize(tx) {
        tx.run('CREATE TABLE discarded(value)');
        throw new Error('initializer failed');
      },
    }),
  ).toThrow('initializer failed');
  // Generic transaction rollback is covered elsewhere. The unique boundary here is
  // that initialization failure never publishes a compatible format for a later open.
  expect(() => assertDatabaseVersion(path, 5, 'unsupported spool format')).toThrow(
    'unsupported spool format',
  );
});
