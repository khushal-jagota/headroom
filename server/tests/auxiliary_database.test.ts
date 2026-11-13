/** Dedicated schemas, maintenance, and timeout restoration are absent from the
 * existing app-door tests. Use temporary SQLite files and stdlib Python only.
 */
import { afterEach, expect, test } from 'vitest';
import { execFileSync, spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  createAuxiliaryDatabase,
  createDatabase,
  createReadAccess,
  registerReadFunction,
  is_sqlite_error,
} from '../core/database/index.ts';
import { MacInbox } from '../executor/vps_inbox.ts';
import { InboxStore } from '../executor/data/inbox.ts';
import { FileStaging } from '../executor/vps_files.ts';
import type { WireValue } from '../executor/contracts.ts';
import type {
  AuxiliaryDatabase,
  AuxiliaryTransactionSql,
  ReadAccess,
} from '../core/database/contracts.ts';

const cleanup: Array<() => void | Promise<void>> = [];
afterEach(async () => {
  for (const close of cleanup.splice(0).reverse()) await close();
});
function path() {
  const root = mkdtempSync(join(tmpdir(), 'panels-auxiliary-'));
  cleanup.push(() => rmSync(root, { recursive: true, force: true }));
  return join(root, 'metrics.sqlite3');
}
function own<T extends ReadAccess>(database: T): T {
  cleanup.push(() => database.close());
  return database;
}
function open(
  filename: string,
  options: Partial<Parameters<typeof createAuxiliaryDatabase>[1]> = {},
) {
  return own(
    createAuxiliaryDatabase(filename, {
      schemaVersion: 2,
      maxBytes: 4 * 1024 * 1024,
      initialize: (tx) => {
        tx.run('CREATE TABLE IF NOT EXISTS samples(value BLOB)');
      },
      ...options,
    }),
  );
}
function python(filename: string, source: string): string {
  return execFileSync('python3', ['-c', source, filename], { encoding: 'utf8' }).trim();
}
function observed(filename: string) {
  return JSON.parse(
    python(
      filename,
      `import sqlite3,sys,json
with sqlite3.connect(sys.argv[1]) as conn:
 print(json.dumps(dict(version=conn.execute('PRAGMA user_version').fetchone()[0],
  journal=conn.execute('PRAGMA journal_mode').fetchone()[0],vacuum=conn.execute('PRAGMA auto_vacuum').fetchone()[0],
  tables=conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall())))`,
    ),
  );
}
async function holdWriter(filename: string): Promise<void> {
  const child = spawn(
    'python3',
    [
      '-u',
      '-c',
      `import sqlite3,sys
conn=sqlite3.connect(sys.argv[1]);conn.execute('BEGIN IMMEDIATE');print('ready',flush=True)
sys.stdin.readline();conn.rollback();conn.close()`,
      filename,
    ],
    { stdio: ['pipe', 'pipe', 'pipe'] },
  );
  const exited = new Promise<void>((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code) =>
      code === 0 ? resolve() : reject(new Error('synthetic writer failed')),
    );
  });
  cleanup.push(async () => {
    child.stdin.end('\n');
    await exited;
  });
  await new Promise<void>((resolve, reject) => {
    child.once('error', reject);
    child.stdout.once('data', () => resolve());
    child.once('exit', (code) => {
      if (code !== 0) reject(new Error('synthetic writer failed before ready'));
    });
  });
}
function expectBusy(database: AuxiliaryDatabase, minimumMilliseconds: number): void {
  const before = performance.now();
  expect(() => database.write(() => {})).toThrow(/locked/);
  expect(performance.now() - before).toBeGreaterThanOrEqual(minimumMilliseconds);
}

test('auxiliary schema initialization commits atomically and the application factory still requires an existing file', () => {
  const filename = path();
  expect(() => createDatabase(filename)).toThrow();
  expect(() =>
    open(filename, {
      initialize(tx) {
        tx.run('CREATE TABLE discarded(value)');
        throw new Error('schema refused');
      },
    }),
  ).toThrow('schema refused');
  expect(observed(filename)).toMatchObject({ version: 0, tables: [] });
  const database = open(filename);
  expect(database.storageStats()).toMatchObject({
    userVersion: 2,
    autoVacuum: 'incremental',
    journalMode: 'wal',
    maxPageCount: 1024,
  });
  database.write((tx) => tx.run('INSERT INTO samples VALUES (?)', [Buffer.from('sample')]));
  database.close();
  const reopened = open(filename);
  expect(reopened.read((sql) => sql.all('SELECT value FROM samples'))).toEqual([
    { value: Buffer.from('sample') },
  ]);
});

test('unknown versions refuse before persistent settings, rows, or file bytes change', () => {
  const filename = path();
  python(
    filename,
    `import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as conn:
 conn.execute('CREATE TABLE retained(value)');conn.execute("INSERT INTO retained VALUES('keep')");conn.execute('PRAGMA user_version=19')`,
  );
  const bytes = readFileSync(filename);
  const before = observed(filename);
  let initialized = false;
  expect(() =>
    open(filename, {
      initialize() {
        initialized = true;
      },
    }),
  ).toThrow('unsupported auxiliary database schema: 19');
  expect(initialized).toBe(false);
  expect(observed(filename)).toEqual(before);
  expect(readFileSync(filename)).toEqual(bytes);
  expect(
    python(
      filename,
      `import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as conn: print(conn.execute('SELECT value FROM retained').fetchone()[0])`,
    ),
  ).toBe('keep');
});

test('auxiliary overrides restore the configured timeout after success and callback failure', async () => {
  const filename = path();
  const database = open(filename);
  database.write(() => {}, { busyTimeoutMs: 0 });
  await holdWriter(filename);
  expectBusy(database, 350);
  expect(() => database.write(() => {}, { busyTimeoutMs: 0 })).toThrow(/locked/);
  expectBusy(database, 350);
});

test('callback failure and checkpoint overrides preserve custom rather than application timeout', async () => {
  const filename = path();
  const database = open(filename, { busyTimeoutMs: 80 });
  expect(() =>
    database.write(
      () => {
        throw new Error('work refused');
      },
      { busyTimeoutMs: 0 },
    ),
  ).toThrow('work refused');
  database.checkpoint('PASSIVE', { busyTimeoutMs: 0 });
  await holdWriter(filename);
  const before = performance.now();
  expectBusy(database, 40);
  expect(performance.now() - before).toBeLessThan(1000);
  const checkpoint = database.checkpoint('TRUNCATE', { busyTimeoutMs: 0 });
  expect(checkpoint.busy).toBe(1);
  expectBusy(database, 40);
});

test('read snapshots stay readonly while checkpoint reports pinned WAL then truncates after release', () => {
  const filename = path();
  const database = open(filename);
  database.write((tx) => tx.run('INSERT INTO samples VALUES (1)'));
  const reads = own(createReadAccess(filename, { busyTimeoutMs: 25 }));
  reads.read((sql) => {
    expect(sql.get('SELECT count(*) AS n FROM samples')).toEqual({ n: 1 });
    database.write((tx) => tx.run('INSERT INTO samples VALUES (2)'));
    expect(sql.get('SELECT count(*) AS n FROM samples')).toEqual({ n: 1 });
    const passive = database.checkpoint('PASSIVE', { busyTimeoutMs: 0 });
    expect(passive.checkpointed).toBeLessThan(passive.log);
    expect(database.checkpoint('TRUNCATE', { busyTimeoutMs: 0 }).busy).toBe(1);
    expect(() => sql.get('INSERT INTO samples VALUES (3) RETURNING value')).toThrow(/readonly/i);
  });
  expect(database.checkpoint('TRUNCATE')).toEqual({ busy: 0, log: 0, checkpointed: 0 });
  expect(statSync(filename + '-wal').size).toBe(0);
});

test('maintenance stays scoped and auxiliary writers preserve read functions, rollback, and callback lifetimes', () => {
  const database = open(path());
  let retained: AuxiliaryTransactionSql | undefined;
  database.write((tx) => {
    retained = tx;
    registerReadFunction(tx, 'auxiliary_double', (value) => Number(value) * 2);
    expect(tx.get('SELECT auxiliary_double(7) AS value')).toEqual({ value: 14 });
    for (let n = 0; n < 50; n++) tx.run('INSERT INTO samples VALUES (?)', [Buffer.alloc(8192)]);
    expect(() => database.checkpoint('PASSIVE')).toThrow('no active write transaction');
  });
  database.write((tx) => tx.run('DELETE FROM samples'));
  const before = database.storageStats();
  expect(before.freelistCount).toBeGreaterThan(1);
  database.write((tx) => tx.incrementalVacuum(128));
  // Python ignores the returned cursor: 128 is a limit, not 128 consumed steps.
  expect(database.storageStats().freelistCount).toBe(before.freelistCount - 1);
  expect(database.storageStats().pageCount).toBe(before.pageCount - 1);
  expect(() => retained!.incrementalVacuum(1)).toThrow('no longer active');
  expect(() => retained!.run('INSERT INTO samples VALUES (3)')).toThrow('no longer active');
  expect(() => registerReadFunction(retained!, 'expired', () => 1)).toThrow('scope');
  expect(() =>
    database.write(async (tx) => {
      tx.run('INSERT INTO samples VALUES (3)');
    }),
  ).toThrow('must be synchronous');
  expect(database.read((sql) => sql.get('SELECT count(*) AS n FROM samples'))).toEqual({ n: 0 });
  database.close();
  expect(() => database.storageStats()).toThrow('database is closed');
  expect(() => database.checkpoint('PASSIVE')).toThrow('database is closed');
});

test('numeric configuration and maintenance values refuse invalid PRAGMA interpolation', () => {
  const filename = path();
  expect(() => open(filename, { maxBytes: Number.NaN })).toThrow('maxBytes');
  expect(() => open(filename, { schemaVersion: 2.5 })).toThrow('schemaVersion');
  const database = open(filename);
  expect(() => database.write(() => {}, { busyTimeoutMs: Number.POSITIVE_INFINITY })).toThrow(
    'busyTimeoutMs',
  );
  expect(() => database.checkpoint('PASSIVE', { busyTimeoutMs: -1 })).toThrow('busyTimeoutMs');
  expect(() => database.write((tx) => tx.incrementalVacuum(-1))).toThrow('pages');
  expect(database.storageStats().userVersion).toBe(2);
});

// SQLite FULL can abort the whole transaction and destroy every savepoint. The
// cleanup errors must not hide its class and turn a retryable item into a refusal.
test('a whole-transaction storage failure inside savepoints remains retryable at inbox admission', async () => {
  const filename = path(),
    database = open(filename);
  let original: unknown;
  class FullInbox extends MacInbox {
    override async accept(): Promise<WireValue<'AcceptanceReceipt'>> {
      return database.write((tx) =>
        tx.savepoint((outer) =>
          outer.savepoint((inner) => {
            try {
              inner.run('INSERT INTO samples VALUES (zeroblob(?))', [8 * 1024 * 1024]);
            } catch (error) {
              original = error;
              throw error;
            }
            throw new Error('the synthetic storage budget did not fail');
          }),
        ),
      );
    }
  }
  const inbox = new FullInbox(
    new InboxStore(database, new FileStaging(filename)),
    {
      now: () => new Date(0),
      nowUnix: () => 0,
      isoformat: () => '1970-01-01T00:00:00+00:00',
    },
    {
      authority: () => {
        throw new Error('not used');
      },
      validate_stop: () => {},
      accept_stop_receipt: async () => {},
    },
  );
  try {
    const reply = await inbox.accept_batch(
      'conversation',
      {
        sender_id: 'sender',
        items: [
          {
            sender_refusal: null,
            envelope: {
              conversation_id: 'conversation',
              sender_id: 'sender',
              sequence: 1,
              submission_id: 'submission',
              kind: 'backend_event',
              payload: {},
              file_dependencies: [],
            },
          },
        ],
      },
      {
        executor_incarnation: 'fixture',
        protocol_version: 22,
        spool_format_version: 5,
        application_revision: 'fixture',
        connection_generation: 0,
      },
    );
    expect(reply.receipts).toEqual([]);
    expect(reply.stop?.reason).toBe('retry_later');
    expect(is_sqlite_error(original)).toBe(true);
    expect(original).toMatchObject({ code: 'SQLITE_FULL' });
    expect(
      (original as Error & { savepoint_cleanup_errors: unknown[] }).savepoint_cleanup_errors,
    ).toHaveLength(4);
    expect(database.read((sql) => sql.all('SELECT * FROM samples'))).toEqual([]);
    database.write((tx) => tx.run('INSERT INTO samples VALUES (?)', [Buffer.from('later retry')]));
  } finally {
    await inbox.stop({ deadline: performance.now() / 1000 + 5 });
  }
});
