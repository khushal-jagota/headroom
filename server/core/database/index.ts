import { assert_test_database_path } from '../test_paths.ts';
import BetterSqlite3 from 'better-sqlite3';
import { closeSync, existsSync, mkdirSync, openSync } from 'node:fs';
import { dirname } from 'node:path';
import type {
  CommitStep,
  Database,
  ReadAccess,
  ReadSql,
  SqlParameters,
  SqlValue,
  WriteOptions,
  TransactionSql,
  AuxiliaryDatabase,
  AuxiliaryDatabaseOptions,
  AuxiliaryTransactionSql,
  StorageStats,
  CheckpointResult,
  ApplicationSchemaSeed,
  FreshDatabaseBootstrap,
  DurableSidecarDatabaseOptions,
  DatabaseCloseHandle,
} from './contracts.ts';
export type * from './contracts.ts';

/** Keep native SQLite error identity behind the database boundary. */
export function is_sqlite_error(error: unknown): boolean {
  return error instanceof BetterSqlite3.SqliteError;
}

const functionScopes = new WeakMap<
  ReadSql,
  (name: string, callback: (...values: SqlValue[]) => SqlValue) => void
>();
/** Register a pure SQLite function for this read scope only, without a raw connection. */
export function registerReadFunction(
  sql: ReadSql,
  name: string,
  callback: (...values: SqlValue[]) => SqlValue,
): void {
  const register = functionScopes.get(sql);
  if (!register) throw new TypeError('database SQL function scope is no longer active');
  register(name, callback);
}

const BUSY_TIMEOUT_MS = 5000;
const AUXILIARY_BUSY_TIMEOUT_MS = 500;
const maintenance = new WeakMap<Database, (mode: 'PASSIVE' | 'TRUNCATE') => CheckpointResult>();
function integer(
  value: number,
  name: string,
  minimum: number,
  maximum = Number.MAX_SAFE_INTEGER,
): number {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new TypeError(`${name} must be an integer between ${minimum} and ${maximum}`);
  }
  return value;
}
function busyTimeout(value: number): number {
  return integer(value, 'busyTimeoutMs', 0, 2147483647);
}

function assertSynchronous<T>(result: T): T {
  if (
    result !== null &&
    (typeof result === 'object' || typeof result === 'function') &&
    typeof (result as { then?: unknown }).then === 'function'
  ) {
    // An async callback can reject after its SQL scope expires. Observe that rejection.
    if (result instanceof Promise) void result.catch(() => {});
    throw new TypeError('database callbacks and commit steps must be synchronous');
  }
  return result;
}

function firstWord(sql: string): string {
  const withoutLeadingComments = sql.replace(/^(?:[\s;]|--[^\n]*(?:\n|$)|\/\*[\s\S]*?\*\/)+/, '');
  return /^[A-Za-z]+/.exec(withoutLeadingComments)?.[0]?.toUpperCase() ?? '';
}

function assertScopedStatement(sql: string): void {
  // SQLite has no authorizer in this driver. Never expose a prepared statement or
  // a driver connection, and keep transaction ownership and connection setup here.
  if (
    ![
      'SELECT',
      'WITH',
      'VALUES',
      'INSERT',
      'UPDATE',
      'DELETE',
      'REPLACE',
      'CREATE',
      'ALTER',
      'DROP',
      'EXPLAIN',
      'ANALYZE',
      'REINDEX',
    ].includes(firstWord(sql))
  ) {
    throw new TypeError(
      'transaction control and connection configuration belong to the write door',
    );
  }
}

function invoke<Row>(
  statement: BetterSqlite3.Statement,
  method: 'get' | 'all' | 'run',
  parameters?: SqlParameters,
): Row {
  const values = parameters ?? [];
  return (Array.isArray(values) ? statement[method](...values) : statement[method](values)) as Row;
}

function scope<T, Sql extends ReadSql = TransactionSql>(
  connection: BetterSqlite3.Database,
  writable: boolean,
  work: (sql: Sql) => T,
  auxiliary = false,
  safeIntegers = false,
): T {
  let active = true;
  const clearFunctions: (() => void)[] = [];
  function prepare(sql: string): BetterSqlite3.Statement {
    if (!active) throw new TypeError('database SQL scope is no longer active');
    assertScopedStatement(sql);
    const statement = connection.prepare(sql);
    return safeIntegers ? statement.safeIntegers(true) : statement;
  }
  const reader: ReadSql = Object.freeze({
    get: <Row>(sql: string, parameters?: SqlParameters): Row | undefined =>
      invoke<Row | undefined>(prepare(sql), 'get', parameters),
    all: <Row>(sql: string, parameters?: SqlParameters): Row[] =>
      invoke<Row[]>(prepare(sql), 'all', parameters),
  });
  const capability = writable
    ? Object.freeze({
        ...reader,
        run: (sql: string, parameters?: SqlParameters) =>
          invoke<BetterSqlite3.RunResult>(prepare(sql), 'run', parameters),
        ...(auxiliary
          ? {
              incrementalVacuum(pages: number): void {
                if (!active) throw new TypeError('database SQL scope is no longer active');
                if (!connection.inTransaction)
                  throw new TypeError('incremental vacuum requires a write transaction');
                integer(pages, 'pages', 1);
                // Python execute() discards the cursor after its first SQLite step.
                // run() steps once and resets; pragma() would consume every page.
                connection.prepare(`PRAGMA incremental_vacuum(${pages})`).run();
              },
            }
          : {}),
      })
    : reader;
  functionScopes.set(capability, (name, callback) => {
    if (!active) throw new TypeError('database SQL scope is no longer active');
    let current: typeof callback | undefined = callback;
    clearFunctions.push(() => {
      current = undefined;
    });
    connection.function(name, { varargs: true }, (...values: SqlValue[]) => {
      if (!active || !current)
        throw new TypeError('database SQL function scope is no longer active');
      return current(...values);
    });
  });
  try {
    return assertSynchronous(work(capability as unknown as Sql));
  } finally {
    functionScopes.delete(capability);
    active = false;
    for (const clear of clearFunctions) clear();
  }
}

/** A worker thread can open this capability without a writable connection. */
export function createReadAccess(
  path: string,
  options: { busyTimeoutMs?: number } = {},
): ReadAccess {
  return createConfiguredReadAccess(path, options);
}

function closeCapability(close: () => void): DatabaseCloseHandle & { readonly closed: boolean } {
  let closed = false;
  return {
    get closed() {
      return closed;
    },
    close() {
      if (!closed) {
        close();
        closed = true;
      }
    },
  };
}

function createConfiguredReadAccess(
  path: string,
  options: { busyTimeoutMs?: number },
  durable?: { foreignKeys: boolean },
  adopt?: (cleanup: DatabaseCloseHandle) => void,
): ReadAccess {
  path = assert_test_database_path(path);
  const connection = new BetterSqlite3(path, {
    readonly: true,
    fileMustExist: true,
    timeout: busyTimeout(options.busyTimeoutMs ?? BUSY_TIMEOUT_MS),
  });
  let reading = false;
  const cleanup = closeCapability(() => {
    if (reading) throw new TypeError('cannot close an active database scope');
    connection.close();
  });
  try {
    adopt?.(cleanup);
    connection.pragma('query_only = ON');
    if (durable) {
      connection.pragma(`foreign_keys = ${durable.foreignKeys ? 'ON' : 'OFF'}`);
      connection.pragma('trusted_schema = OFF');
    } else {
      connection.pragma('foreign_keys = ON');
      connection.pragma('recursive_triggers = ON');
    }
  } catch (error) {
    try {
      cleanup.close();
    } catch (cleanup_error) {
      if (adopt === undefined) throw cleanup_error;
      const failure = error instanceof Error ? error : new Error(String(error));
      Object.assign(failure, { cleanup_error });
      throw failure;
    }
    throw error;
  }
  return Object.freeze({
    read<T>(work: (sql: ReadSql) => T): T {
      if (cleanup.closed) throw new Error('database is closed');
      if (reading)
        throw new TypeError('nested read callbacks are not allowed; use the current read scope');
      reading = true;
      try {
        connection.exec('BEGIN');
        const result = scope(connection, false, work, false, durable !== undefined);
        connection.exec('COMMIT');
        return result;
      } catch (error) {
        if (connection.inTransaction) connection.exec('ROLLBACK');
        throw error;
      } finally {
        reading = false;
      }
    },
    close(): void {
      cleanup.close();
    },
  });
}

/** Opens an existing Python-created database. No schema creation or migration. */
export function createDatabase(
  path: string,
  options: {
    commitSteps?: readonly CommitStep[];
    onChange?: () => void;
  } = {},
): Database {
  path = assert_test_database_path(path);
  const writer = new BetterSqlite3(path, { fileMustExist: true, timeout: BUSY_TIMEOUT_MS });
  let reader: ReadAccess;
  try {
    writer.pragma('journal_mode = WAL');
    writer.pragma('synchronous = FULL');
    writer.pragma('foreign_keys = ON');
    writer.pragma('recursive_triggers = ON');
    reader = createReadAccess(path);
  } catch (error) {
    writer.close();
    throw error;
  }
  return createOwnedDatabase<TransactionSql>(writer, reader, {
    ...options,
    busyTimeoutMs: BUSY_TIMEOUT_MS,
  }).database;
}

/** A private native connection owns the source until the online image copy settles. */
export async function onlineBackup(sourcePath: string, destinationPath: string): Promise<void> {
  sourcePath = assert_test_database_path(sourcePath);
  destinationPath = assert_test_database_path(destinationPath);
  const source = new BetterSqlite3(sourcePath, {
    readonly: true,
    fileMustExist: true,
    timeout: BUSY_TIMEOUT_MS,
  });
  try {
    // A source write between bounded steps restarts SQLite's online copy. Transfer
    // the largest positive SQLite int in one read step instead of yielding per 100 pages.
    // better-sqlite3 clamps negative callback results to zero, so -1 cannot request all.
    await source.backup(destinationPath, { progress: () => 0x7fffffff });
  } finally {
    source.close();
  }
}

export function verifyDatabaseIntegrity(path: string): void {
  path = assert_test_database_path(path);
  const connection = new BetterSqlite3(path, {
    readonly: true,
    fileMustExist: true,
    timeout: BUSY_TIMEOUT_MS,
  });
  try {
    if (connection.pragma('integrity_check', { simple: true }) !== 'ok')
      throw new Error(`SQLite integrity check failed for ${path}`);
  } finally {
    connection.close();
  }
}

export function checkpointDatabase(
  database: Database,
  mode: 'PASSIVE' | 'TRUNCATE',
): CheckpointResult {
  const checkpoint = maintenance.get(database);
  if (!checkpoint) throw new TypeError('database capability is not owned by core');
  return checkpoint(mode);
}

/** Installs trusted build-time DDL and fresh domain state through the canonical writer. */
export function createDatabaseFromSeed(
  path: string,
  seed: ApplicationSchemaSeed,
  bootstrap: FreshDatabaseBootstrap,
): Database {
  path = assert_test_database_path(path);
  // Exclusive creation refuses existing files and symlinks before any SQLite settings run.
  closeSync(openSync(path, 'wx', 0o644));
  const database = createDatabase(path);
  try {
    database.write((tx) => {
      for (const statement of seed.statements) tx.run(statement);
      tx.run('DELETE FROM alembic_version');
      tx.run('INSERT INTO alembic_version(version_num) VALUES (?)', [seed.alembicRevision]);
      assertSynchronous(bootstrap(tx));
    });
    return database;
  } catch (error) {
    database.close();
    throw error;
  }
}

/** Both application and auxiliary stores enter the same transaction engine. */
function createOwnedDatabase<Tx extends TransactionSql>(
  writer: BetterSqlite3.Database,
  reader: ReadAccess,
  options: {
    busyTimeoutMs: number;
    commitSteps?: readonly CommitStep[];
    onChange?: () => void;
    auxiliary?: boolean;
    safeIntegers?: boolean;
  },
) {
  const commitSteps = [...(options.commitSteps ?? [])];
  let writing = false;
  let reading = false;
  let closed = false;
  const checkOpen = () => {
    if (closed) throw new Error('database is closed');
  };
  const database = Object.freeze({
    write<T>(work: (tx: Tx) => T, writeOptions?: WriteOptions): T {
      checkOpen();
      if (writing)
        throw new TypeError('nested write door calls are not allowed; use the current transaction');
      if (writeOptions?.busyTimeoutMs !== undefined) busyTimeout(writeOptions.busyTimeoutMs);
      writing = true;
      let result: T;
      let changed = false;
      try {
        if (writeOptions?.busyTimeoutMs !== undefined)
          writer.pragma(`busy_timeout = ${writeOptions.busyTimeoutMs}`);
        writer.exec('BEGIN IMMEDIATE');
        const before = writer.prepare('SELECT total_changes() AS count').get() as { count: number };
        result = scope(writer, true, work, options.auxiliary, options.safeIntegers);
        for (const step of commitSteps) scope(writer, true, step);
        const after = writer.prepare('SELECT total_changes() AS count').get() as { count: number };
        changed = after.count !== before.count;
        writer.exec('COMMIT');
      } catch (error) {
        if (writer.inTransaction) writer.exec('ROLLBACK');
        throw error;
      } finally {
        try {
          if (writeOptions?.busyTimeoutMs !== undefined)
            writer.pragma(`busy_timeout = ${options.busyTimeoutMs}`);
        } finally {
          writing = false;
        }
      }
      if (changed && writeOptions?.changeSignal !== 'silent') {
        try {
          options.onChange?.();
        } catch (error) {
          console.error('post-commit change signal failed', error);
        }
      }
      return result;
    },
    read<T>(work: (sql: ReadSql) => T): T {
      checkOpen();
      if (reading)
        throw new TypeError('nested read callbacks are not allowed; use the current read scope');
      reading = true;
      try {
        return reader.read(work);
      } finally {
        reading = false;
      }
    },
    close(): void {
      if (closed) return;
      if (writing) throw new TypeError('cannot close an active database scope');
      reader.close();
      writer.close();
      closed = true;
    },
  });
  maintenance.set(database, (mode) => {
    outsideTransaction();
    if (mode !== 'PASSIVE' && mode !== 'TRUNCATE')
      throw new TypeError('unsupported checkpoint mode');
    return (writer.pragma(`wal_checkpoint(${mode})`) as CheckpointResult[])[0];
  });
  function outsideTransaction(): void {
    checkOpen();
    if (writing || writer.inTransaction)
      throw new TypeError('database maintenance requires no active write transaction');
    if (reading) throw new TypeError('database maintenance requires no active read transaction');
  }
  return {
    database,
    outsideTransaction,
  };
}

function requireDatabaseVersion(
  connection: BetterSqlite3.Database,
  expectedVersion: number,
  refusalMessage: string,
): void {
  if (Number(connection.pragma('user_version', { simple: true })) !== expectedVersion)
    throw Object.assign(new Error(refusalMessage), { name: 'ValueError' });
}

/** Check an existing format before its caller creates directories or takes a process lock. */
export function assertDatabaseVersion(
  path: string,
  expectedVersion: number,
  refusalMessage: string,
): boolean {
  path = assert_test_database_path(path);
  integer(expectedVersion, 'schemaVersion', 1, 2147483647);
  if (!existsSync(path)) return false;
  const connection = new BetterSqlite3(path, {
    readonly: true,
    fileMustExist: true,
    timeout: BUSY_TIMEOUT_MS,
  });
  try {
    requireDatabaseVersion(connection, expectedVersion, refusalMessage);
    return true;
  } finally {
    connection.close();
  }
}

/** Frozen sidecars reuse the writer engine, with exact versions and lossless integer rows. */
export function createDurableSidecarDatabase(
  path: string,
  options: DurableSidecarDatabaseOptions,
): Database {
  path = assert_test_database_path(path);
  const expectedVersion = integer(options.schemaVersion, 'schemaVersion', 1, 2147483647);
  const existed = assertDatabaseVersion(path, expectedVersion, options.refusalMessage);
  // The caller owns its parent directory and process lock. Never claim another creator's file.
  if (!existed) closeSync(openSync(path, 'wx', 0o644));
  const writer = new BetterSqlite3(path, { fileMustExist: true, timeout: BUSY_TIMEOUT_MS });
  const writer_cleanup = closeCapability(() => writer.close());
  let reader_cleanup: DatabaseCloseHandle | undefined;
  let reader: ReadAccess | undefined;
  try {
    // Existing version zero is refused. Only our exclusive creation can initialize version zero.
    requireDatabaseVersion(writer, existed ? expectedVersion : 0, options.refusalMessage);
    writer.pragma('journal_mode = WAL');
    writer.pragma('synchronous = FULL');
    writer.pragma('fullfsync = ON');
    writer.pragma('trusted_schema = OFF');
    writer.pragma(`foreign_keys = ${options.foreignKeys ? 'ON' : 'OFF'}`);
    reader = createConfiguredReadAccess(
      path,
      { busyTimeoutMs: BUSY_TIMEOUT_MS },
      options,
      (cleanup) => {
        reader_cleanup = cleanup;
      },
    );
    const owned = createOwnedDatabase<TransactionSql>(writer, reader, {
      busyTimeoutMs: BUSY_TIMEOUT_MS,
      safeIntegers: true,
    });
    if (!existed) {
      owned.database.write((tx) => {
        assertSynchronous(options.initialize(tx));
        writer.pragma(`user_version = ${expectedVersion}`);
      });
    }
    return owned.database;
  } catch (error) {
    const cleanup: DatabaseCloseHandle = {
      close() {
        let failure: unknown;
        for (const connection of [reader_cleanup, writer_cleanup])
          if (connection !== undefined)
            try {
              connection.close();
            } catch (close_error) {
              failure ??= close_error;
            }
        if (failure !== undefined) throw failure;
      },
    };
    try {
      cleanup.close();
    } catch (cleanup_error) {
      const failure = error instanceof Error ? error : new Error(String(error));
      Object.assign(failure, { database_cleanup: cleanup, cleanup_error });
      throw failure;
    }
    throw error;
  }
}

/** Domain-owned sidecar schema; application schema creation remains Python-owned. */
export function createAuxiliaryDatabase(
  path: string,
  options: AuxiliaryDatabaseOptions,
): AuxiliaryDatabase {
  path = assert_test_database_path(path);
  const expectedVersion = integer(options.schemaVersion, 'schemaVersion', 1, 2147483647);
  const maxBytes = integer(options.maxBytes, 'maxBytes', 1);
  const timeout = busyTimeout(options.busyTimeoutMs ?? AUXILIARY_BUSY_TIMEOUT_MS);
  const admit = (connection: BetterSqlite3.Database) => {
    const version = Number(connection.pragma('user_version', { simple: true }));
    if (version !== 0 && version !== expectedVersion) {
      throw new Error(`unsupported auxiliary database schema: ${version}`);
    }
  };
  if (existsSync(path)) {
    const preflight = new BetterSqlite3(path, { readonly: true, fileMustExist: true, timeout });
    try {
      admit(preflight);
    } finally {
      preflight.close();
    }
  }
  mkdirSync(dirname(path), { recursive: true });
  const writer = new BetterSqlite3(path, { timeout });
  let reader: ReadAccess | undefined;
  try {
    // Recheck before persistent settings in case the version changed after preflight.
    admit(writer);
    writer.pragma('auto_vacuum = INCREMENTAL');
    const journalMode = writer.pragma('journal_mode = WAL', { simple: true });
    if (journalMode !== 'wal') throw new Error('auxiliary database requires WAL mode');
    const pageSize = Number(writer.pragma('page_size', { simple: true }));
    writer.pragma(`max_page_count = ${Math.max(1, Math.floor(maxBytes / pageSize))}`);
    writer.pragma('synchronous = FULL');
    writer.pragma('foreign_keys = ON');
    writer.pragma('recursive_triggers = ON');
    reader = createReadAccess(path, { busyTimeoutMs: timeout });
    const owned = createOwnedDatabase<AuxiliaryTransactionSql>(writer, reader, {
      busyTimeoutMs: timeout,
      auxiliary: true,
    });
    owned.database.write((tx) => {
      assertSynchronous(options.initialize(tx));
      // user_version participates in this same transaction as the domain DDL.
      writer.pragma(`user_version = ${expectedVersion}`);
    });
    return Object.freeze({
      ...owned.database,
      storageStats(): StorageStats {
        owned.outsideTransaction();
        return {
          pageSize: Number(writer.pragma('page_size', { simple: true })),
          pageCount: Number(writer.pragma('page_count', { simple: true })),
          freelistCount: Number(writer.pragma('freelist_count', { simple: true })),
          maxPageCount: Number(writer.pragma('max_page_count', { simple: true })),
          userVersion: Number(writer.pragma('user_version', { simple: true })),
          autoVacuum: (['none', 'full', 'incremental'] as const)[
            Number(writer.pragma('auto_vacuum', { simple: true }))
          ],
          journalMode: String(writer.pragma('journal_mode', { simple: true })),
        };
      },
      checkpoint(
        mode: 'PASSIVE' | 'TRUNCATE',
        override: { busyTimeoutMs?: number } = {},
      ): CheckpointResult {
        owned.outsideTransaction();
        if (mode !== 'PASSIVE' && mode !== 'TRUNCATE')
          throw new TypeError('unsupported checkpoint mode');
        if (override.busyTimeoutMs !== undefined) busyTimeout(override.busyTimeoutMs);
        try {
          if (override.busyTimeoutMs !== undefined)
            writer.pragma(`busy_timeout = ${override.busyTimeoutMs}`);
          return (writer.pragma(`wal_checkpoint(${mode})`) as CheckpointResult[])[0];
        } finally {
          if (override.busyTimeoutMs !== undefined) writer.pragma(`busy_timeout = ${timeout}`);
        }
      },
    });
  } catch (error) {
    reader?.close();
    writer.close();
    throw error;
  }
}
