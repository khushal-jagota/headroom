import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { createDatabase } from '../core/database/index.ts';
import type { Database, ReadSql, TransactionSql } from '../core/database/index.ts';
import { ChangeSignal } from '../core/changes/index.ts';

const cleanup: Array<() => void> = [];
afterEach(() => {
  for (const close of cleanup.splice(0).reverse()) close();
});
function fixture(): string {
  const root = mkdtempSync(join(tmpdir(), 'panels-write-door-'));
  cleanup.push(() => rmSync(root, { recursive: true, force: true }));
  const path = join(root, 'database.sqlite3');
  execFileSync('python3', [
    '-c',
    `
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as conn:
    conn.execute('CREATE TABLE source (value INTEGER NOT NULL)')
    conn.execute('CREATE TABLE derived (value INTEGER NOT NULL)')
`,
    path,
  ]);
  return path;
}
function rowsFromPython(path: string, table: 'source' | 'derived'): number[] {
  return JSON.parse(
    execFileSync(
      'python3',
      [
        '-c',
        `
import json, sqlite3, sys
with sqlite3.connect(sys.argv[1]) as conn:
    print(json.dumps([row[0] for row in conn.execute('SELECT value FROM ${table} ORDER BY value')]))
`,
        path,
      ],
      { encoding: 'utf8' },
    ),
  ) as number[];
}
function own(database: Database): Database {
  cleanup.push(() => database.close());
  return database;
}

test('commit step sees uncommitted source rows; Python sees source and derived only after commit', () => {
  const path = fixture();
  const signal = new ChangeSignal();
  const observed: number[][] = [];
  signal.subscribe(() => {
    observed.push(rowsFromPython(path, 'derived'));
  });
  const database = own(
    createDatabase(path, {
      onChange: () => signal.emit(),
      commitSteps: [
        (sql) => {
          expect(sql.get<{ value: number }>('SELECT value FROM source')?.value).toBe(7);
          expect(rowsFromPython(path, 'source')).toEqual([]);
          sql.run('INSERT INTO derived SELECT value FROM source');
        },
      ],
    }),
  );
  expect(database.write((sql) => sql.run('INSERT INTO source VALUES (?)', [7]).changes)).toBe(1);
  expect(rowsFromPython(path, 'source')).toEqual([7]);
  expect(rowsFromPython(path, 'derived')).toEqual([7]);
  expect(observed).toEqual([[7]]);
});

test('failed commit step or asynchronous work rolls the whole transaction back and emits nothing', () => {
  const path = fixture();
  let signals = 0;
  const database = own(
    createDatabase(path, {
      onChange: () => {
        signals++;
      },
      commitSteps: [
        (sql) => {
          sql.run('INSERT INTO derived VALUES (8)');
          throw new Error('projection failed');
        },
      ],
    }),
  );
  expect(() => database.write((sql) => sql.run('INSERT INTO source VALUES (8)'))).toThrow(
    'projection failed',
  );
  expect(() =>
    database.write(async (sql) => {
      sql.run('INSERT INTO source VALUES (9)');
    }),
  ).toThrow('must be synchronous');
  expect(rowsFromPython(path, 'source')).toEqual([]);
  expect(rowsFromPython(path, 'derived')).toEqual([]);
  expect(signals).toBe(0);
});

test('read scopes cannot write or retain SQL access; transaction control stays inside the door', () => {
  const path = fixture();
  let signals = 0;
  const database = own(
    createDatabase(path, {
      onChange: () => {
        signals++;
      },
    }),
  );
  let retainedRead: ReadSql | undefined;
  let retainedWrite: TransactionSql | undefined;
  database.read((sql) => {
    retainedRead = sql;
    expect('run' in sql).toBe(false);
    expect(() => sql.get('INSERT INTO source VALUES (1) RETURNING value')).toThrow(/readonly/i);
  });
  database.write(
    (sql) => {
      retainedWrite = sql;
      expect(() => sql.run('/* comment */ ; -- comment\n COMMIT')).toThrow(/write door/);
      expect(() => database.write(() => {})).toThrow(/nested/);
      sql.run('INSERT INTO source VALUES (2)');
    },
    { changeSignal: 'silent' },
  );
  database.write(() => {});
  expect(signals).toBe(0);
  expect(() => retainedRead?.get('SELECT 1')).toThrow(/no longer active/);
  expect(() => retainedWrite?.run('INSERT INTO source VALUES (3)')).toThrow(/no longer active/);
  expect(rowsFromPython(path, 'source')).toEqual([2]);
});

// Existing whole-write rollback coverage does not prove that a refusal receipt can
// commit after a nested domain rollback, or that nested capabilities expire.
test('savepoints isolate nested failures and reject asynchronous or expired scopes', () => {
  const database = own(createDatabase(fixture()));
  let retained: TransactionSql | undefined;
  database.write((outer) => {
    outer.run('INSERT INTO source VALUES (1)');
    outer.savepoint((middle) => {
      retained = middle;
      middle.run('INSERT INTO source VALUES (2)');
      expect(() =>
        middle.savepoint((inner) => {
          inner.run('INSERT INTO source VALUES (3)');
          throw new Error('domain refused');
        }),
      ).toThrow('domain refused');
      expect(() =>
        middle.savepoint((inner) => {
          inner.run('INSERT INTO source VALUES (4)');
          return Promise.resolve();
        }),
      ).toThrow('must be synchronous');
      middle.run('INSERT INTO derived VALUES (9)');
    });
    expect(() => retained!.savepoint(() => {})).toThrow('scope is no longer active');
  });
  expect(database.read((sql) => sql.all('SELECT value FROM source ORDER BY value'))).toEqual([
    { value: 1 },
    { value: 2 },
  ]);
  expect(database.read((sql) => sql.all('SELECT value FROM derived'))).toEqual([{ value: 9 }]);
});
