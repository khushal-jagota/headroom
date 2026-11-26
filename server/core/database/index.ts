import { count_rows, timed_task } from '../slow_tasks.ts';
import { assert_test_database_path } from '../test_paths.ts';
import BetterSqlite3 from 'better-sqlite3';
import { closeSync, existsSync, mkdirSync, openSync, statSync } from 'node:fs';
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
import { select_schema_steps } from './schema_steps/contracts.ts';
import {
  LIVE_NAVIGATION_VIEW_ORDERED_TABLE,
  live_baseline_variant,
} from './schema_steps/live_baseline_variant.ts';
import type { SchemaStep, SchemaStepOutcome, SchemaTransaction } from './schema_steps/contracts.ts';
export type * from './contracts.ts';

/** Keep native SQLite error identity behind the database boundary. */
export function is_sqlite_error(error: unknown): boolean {
  return error instanceof BetterSqlite3.SqliteError;
}

const functionScopes = new WeakMap<
  ReadSql,
  (name: string, callback: (...values: SqlValue[]) => SqlValue) => void
>();
interface SavepointWitness {
  rolled_back: boolean;
}
const savepointScopes = new WeakMap<BetterSqlite3.Database, SavepointWitness[]>();
const rollbackProofs = new WeakMap<ReadSql, () => () => boolean>();
/** Capture actual enclosing savepoints, never infer rollback from the current rows. */
export function capture_savepoint_rollback(sql: ReadSql): () => boolean {
  return rollbackProofs.get(sql)?.() ?? (() => false);
}
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
let nextSavepoint = 0;
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

function withoutLeadingComments(sql: string): string {
  return sql.replace(/^(?:[\s;]|--[^\n]*(?:\n|$)|\/\*[\s\S]*?\*\/)+/, '');
}

function firstWord(sql: string): string {
  return /^[A-Za-z]+/.exec(withoutLeadingComments(sql))?.[0]?.toUpperCase() ?? '';
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
  const result = (
    Array.isArray(values) ? statement[method](...values) : statement[method](values)
  ) as Row;
  if (method === 'all') count_rows((result as unknown[]).length);
  else if (method === 'run') count_rows((result as BetterSqlite3.RunResult).changes);
  else if (result !== undefined) count_rows(1);
  return result;
}

/** Compiled statements per connection. Most SQL text is fixed, so compiling once saves
 * the work on every later call. The bound keeps generated SQL (IN lists) from growing it. */
const STATEMENT_CACHE_LIMIT = 1000;
const statementCaches = new WeakMap<BetterSqlite3.Database, Map<string, BetterSqlite3.Statement>>();
function compiled(connection: BetterSqlite3.Database, sql: string): BetterSqlite3.Statement {
  let cache = statementCaches.get(connection);
  if (!cache) statementCaches.set(connection, (cache = new Map()));
  let statement = cache.get(sql);
  if (statement) {
    cache.delete(sql);
  } else {
    assertScopedStatement(sql);
    statement = connection.prepare(sql);
    if (cache.size >= STATEMENT_CACHE_LIMIT) cache.delete(cache.keys().next().value!);
  }
  cache.set(sql, statement);
  return statement;
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
    return compiled(connection, sql).safeIntegers(safeIntegers);
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
        savepoint<T>(nested: (tx: TransactionSql) => T): T {
          if (!active) throw new TypeError('database SQL scope is no longer active');
          if (!connection.inTransaction)
            throw new TypeError('savepoint requires a write transaction');
          const name = `panels_scope_${++nextSavepoint}`;
          connection.exec(`SAVEPOINT ${name}`);
          const witness: SavepointWitness = { rolled_back: false };
          const scopes = savepointScopes.get(connection) ?? [];
          savepointScopes.set(connection, scopes);
          scopes.push(witness);
          try {
            const result = scope<T, TransactionSql>(
              connection,
              true,
              nested,
              auxiliary,
              safeIntegers,
            );
            connection.exec(`RELEASE SAVEPOINT ${name}`);
            return result;
          } catch (error) {
            const errors: unknown[] = [error];
            for (const command of [`ROLLBACK TO SAVEPOINT ${name}`, `RELEASE SAVEPOINT ${name}`]) {
              try {
                connection.exec(command);
                if (command.startsWith('ROLLBACK TO')) witness.rolled_back = true;
              } catch (cleanup) {
                errors.push(cleanup);
              }
            }
            if (errors.length > 1 && is_sqlite_error(error)) {
              const previous =
                (error as Error & { savepoint_cleanup_errors?: unknown[] })
                  .savepoint_cleanup_errors ?? [];
              Object.assign(error as Error, {
                savepoint_cleanup_errors: [...previous, ...errors.slice(1)],
              });
              throw error;
            }
            if (errors.length > 1)
              throw new AggregateError(errors, 'Savepoint rollback or release failed');
            throw error;
          } finally {
            scopes.pop();
            if (scopes.length === 0) savepointScopes.delete(connection);
          }
        },
        ...(auxiliary
          ? {
              incrementalVacuum(pages: number): void {
                if (!active) throw new TypeError('database SQL scope is no longer active');
                if (!connection.inTransaction)
                  throw new TypeError('incremental vacuum requires a write transaction');
                integer(pages, 'pages', 1);
                // run() steps once and resets; pragma() would consume every page.
                connection.prepare(`PRAGMA incremental_vacuum(${pages})`).run();
              },
            }
          : {}),
      })
    : reader;
  rollbackProofs.set(capability, () => {
    if (!active || !writable || !connection.inTransaction) return () => false;
    const witnesses = [...(savepointScopes.get(connection) ?? [])];
    return () =>
      active && connection.inTransaction && witnesses.some((witness) => witness.rolled_back);
  });
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
    rollbackProofs.delete(capability);
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
  durable?: { foreignKeys: boolean; safeIntegers?: boolean },
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
        const result = scope(
          connection,
          false,
          work,
          false,
          durable?.safeIntegers ?? durable !== undefined,
        );
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
    // Only this connection writes in normal use, so its page cache survives between transactions
    // and commit steps find re-read pages in memory. The reader's cache is not raised:
    // every commit clears it. A negative value is in KiB: 256 MB.
    writer.pragma('cache_size = -262144');
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

/**
 * Read the main file of a database that has no writer left. A TRUNCATE checkpoint first
 * moves every WAL frame into it. A read transaction then stays open for the whole read, so
 * no checkpoint can change the file. A WAL that gained frames meanwhile means a writer, and
 * the read is refused.
 */
export async function readStoppedDatabaseFile<T>(path: string, read: () => Promise<T>): Promise<T> {
  path = assert_test_database_path(path);
  const connection = new BetterSqlite3(path, { fileMustExist: true, timeout: BUSY_TIMEOUT_MS });
  try {
    const [result] = connection.pragma('wal_checkpoint(TRUNCATE)') as { busy: number }[];
    if (result?.busy !== 0) throw new Error(`Another connection kept ${path} from its checkpoint`);
    const wal_is_empty = () => {
      try {
        return statSync(path + '-wal').size === 0;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') return true;
        throw error;
      }
    };
    connection.exec('BEGIN');
    try {
      connection.prepare('SELECT count(*) FROM sqlite_schema').pluck().get();
      if (!wal_is_empty()) throw new Error(`${path} kept frames in its WAL after its checkpoint`);
      const value = await read();
      if (!wal_is_empty()) throw new Error(`Another connection wrote ${path} during the read`);
      return value;
    } finally {
      connection.exec('COMMIT');
    }
  } finally {
    connection.close();
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

/** The seed's statement order. A fresh and a stepped database must read back the same rows. */
export const SEED_STATEMENT_QUERY =
  "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY " +
  "CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name";

/** Element for element, with no whitespace normalization. */
function sameStatements(actual: readonly string[], expected: readonly string[]): boolean {
  return (
    actual.length === expected.length &&
    actual.every((statement, position) => statement === expected[position])
  );
}

function schemaRevision(connection: BetterSqlite3.Database): string {
  const rows = connection.prepare('SELECT version_num FROM alembic_version').pluck().all();
  if (rows.length !== 1)
    throw new Error(`Schema steps need exactly one alembic_version row, found ${rows.length}`);
  return String(rows[0]);
}

interface SchemaEntry {
  type: string;
  name: string;
  tbl_name: string;
  rootpage: number;
  sql: string | null;
}

function schemaEntries(connection: BetterSqlite3.Database): SchemaEntry[] {
  return connection
    .prepare('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_schema')
    .all() as SchemaEntry[];
}

/** SQLite's OPFLAG_P2ISREG: P2 of this OpenWrite names a register, not a root page. */
const OPEN_WRITE_ROOT_IN_REGISTER = 0x10;

/**
 * The tables one schema step wrote, read from SQLite itself so a step declares nothing.
 * The schema before and after gives every table and index the step created, rebuilt,
 * altered, moved or dropped. Each statement's plan gives every table it writes rows in,
 * including the writes of triggers.
 */
class SchemaStepFootprint {
  readonly #connection: BetterSqlite3.Database;
  readonly #before: SchemaEntry[];
  readonly #written = new Set<string>();

  constructor(connection: BetterSqlite3.Database) {
    this.#connection = connection;
    this.#before = schemaEntries(connection);
  }

  /** Call before the statement runs, while its root pages still name today's tables. */
  record(sql: string): void {
    if (firstWord(sql) === 'EXPLAIN') return;
    const plan = this.#connection.prepare(`EXPLAIN ${withoutLeadingComments(sql)}`).all() as {
      opcode: string;
      p1: number;
      p2: number;
      p3: number;
      p5: number;
    }[];
    const tables = new Map(
      schemaEntries(this.#connection).map((entry) => [entry.rootpage, entry.tbl_name]),
    );
    for (const step of plan) {
      // Database 0 is main. A register root is a b-tree this statement creates, which the
      // schema comparison finds. A DELETE with no WHERE empties the b-tree with Clear.
      const root =
        step.opcode === 'OpenWrite' &&
        step.p3 === 0 &&
        (step.p5 & OPEN_WRITE_ROOT_IN_REGISTER) === 0
          ? step.p2
          : step.opcode === 'Clear' && step.p2 === 0
            ? step.p1
            : undefined;
      const table = root === undefined ? undefined : tables.get(root);
      if (table !== undefined) this.#written.add(table.toLowerCase());
    }
  }

  /** Touched tables that still exist, and touched names that no longer name a table. */
  touched(): { tables: string[]; removed: string[] } {
    const key = (entry: SchemaEntry) => `${entry.type}\0${entry.name.toLowerCase()}`;
    const shape = (entry: SchemaEntry) => `${entry.tbl_name}\0${entry.rootpage}\0${entry.sql}`;
    const after = schemaEntries(this.#connection);
    const before = new Map(this.#before.map((entry) => [key(entry), shape(entry)]));
    const now = new Map(after.map((entry) => [key(entry), shape(entry)]));
    const touched = new Set(this.#written);
    // Triggers and views own no pages. Their table is touched only if it was written.
    for (const [entries, other] of [
      [this.#before, now],
      [after, before],
    ] as const)
      for (const entry of entries)
        if (
          (entry.type === 'table' || entry.type === 'index') &&
          other.get(key(entry)) !== shape(entry)
        )
          touched.add(entry.tbl_name.toLowerCase());
    const existing = new Map(
      after
        .filter((entry) => entry.type === 'table' && entry.rootpage > 0)
        .map((entry) => [entry.name.toLowerCase(), entry.name]),
    );
    const tables: string[] = [],
      removed: string[] = [];
    for (const name of touched) {
      const table = existing.get(name);
      if (table === undefined) removed.push(name);
      else tables.push(table);
    }
    return { tables, removed };
  }
}

/** Foreign keys of the touched tables, and of every table that points at one of them. */
function scopedForeignKeyViolations(
  connection: BetterSqlite3.Database,
  touched: { tables: string[]; removed: string[] },
): number {
  const targets = new Set(
    [...touched.tables, ...touched.removed].map((name) => name.toLowerCase()),
  );
  const references = connection
    .prepare(
      `SELECT DISTINCT m.name AS child, f."table" AS parent FROM sqlite_schema AS m, pragma_foreign_key_list(m.name, 'main') AS f WHERE m.type = 'table' AND m.rootpage > 0`,
    )
    .all() as { child: string; parent: string }[];
  const scope = new Set(touched.tables);
  for (const { child, parent } of references)
    if (targets.has(parent.toLowerCase())) scope.add(child);
  const count = connection
    .prepare("SELECT count(*) FROM pragma_foreign_key_check(?, 'main')")
    .pluck();
  let violations = 0;
  for (const table of scope) violations += Number(count.get(table));
  return violations;
}

/** The schema table and each touched table with its indexes. Untouched pages are not re-read. */
function scopedIntegrityProblems(connection: BetterSqlite3.Database, tables: string[]): string[] {
  const check = connection.prepare("SELECT * FROM pragma_integrity_check(?, 'main')").pluck();
  return ['sqlite_schema', ...tables].flatMap((table) =>
    (check.all(table) as string[]).filter((line) => line !== 'ok'),
  );
}

/**
 * Bring a stopped database to `head_revision`, one transaction per step. Each step
 * either commits whole, with its revision, or leaves the database as it was.
 */
export function applySchemaSteps(
  path: string,
  steps: readonly SchemaStep[],
  options: {
    head_revision: string;
    /** The release seed's statements, checked before the last commit. Null only in the generator. */
    expected_statements: readonly string[] | null;
    /** Refuse before any write when steps are pending. Used when no backup was taken. */
    refuse_pending: boolean;
    /**
     * The Python baseline seed. A database at its revision must hold exactly its
     * statements before the first step runs. Null only where the database was just
     * made from that seed.
     */
    baseline: ApplicationSchemaSeed | null;
  },
): SchemaStepOutcome {
  path = assert_test_database_path(path);
  const connection = new BetterSqlite3(path, { fileMustExist: true, timeout: BUSY_TIMEOUT_MS });
  try {
    // Foreign keys can only change outside a transaction. A table rebuild needs them off.
    connection.pragma('journal_mode = WAL');
    connection.pragma('synchronous = FULL');
    connection.pragma('foreign_keys = OFF');
    if (Number(connection.pragma('foreign_keys', { simple: true })) !== 0)
      throw new Error('Schema steps need foreign keys off');
    const from_revision = schemaRevision(connection);
    const pending = select_schema_steps(steps, from_revision, options.head_revision);
    if (pending.length === 0) return { from_revision, to_revision: from_revision, applied: [] };
    // Steps are written against the baseline's exact text. A live database that Alembic
    // built step by step must match it, or its recorded trigger variant, with or without the
    // navigation table the server created at run time, or the final seed check could never
    // pass. Table names are unique, so the filter removes at most that one statement.
    if (options.baseline !== null && from_revision === options.baseline.alembicRevision) {
      const actual = (connection.prepare(SEED_STATEMENT_QUERY).pluck().all() as string[]).filter(
        (statement) => statement !== LIVE_NAVIGATION_VIEW_ORDERED_TABLE,
      );
      const variant = live_baseline_variant(options.baseline.statements);
      if (
        !sameStatements(actual, options.baseline.statements) &&
        (variant === null || !sameStatements(actual, variant))
      )
        throw new Error('The database schema text differs from the baseline, so no step ran');
    }
    if (options.refuse_pending)
      throw new Error(
        `The database needs ${pending.length} schema step(s), but no backup was taken, so none ran`,
      );
    const applied: string[] = [];
    for (const [index, step] of pending.entries()) {
      connection.exec('BEGIN IMMEDIATE');
      let active = true;
      let integrity_scope: string[] = [];
      try {
        const footprint = new SchemaStepFootprint(connection);
        const revision = schemaRevision(connection);
        if (revision !== step.previous_revision)
          throw new Error(
            `Schema step ${step.revision} expects ${step.previous_revision}, but the database is at ${revision}`,
          );
        const transaction: SchemaTransaction = Object.freeze({
          run(sql: string): void {
            if (!active) throw new TypeError('schema step transaction is no longer active');
            assertScopedStatement(sql);
            footprint.record(sql);
            connection.prepare(sql).run();
          },
        });
        try {
          assertSynchronous(step.apply(transaction));
        } finally {
          active = false;
        }
        // The update's snapshot checked the whole file just before, in the same stopped
        // window, so each check reads only what this step touched.
        const touched = footprint.touched();
        integrity_scope = [...new Set([...touched.tables, 'alembic_version'])];
        const violations = scopedForeignKeyViolations(connection, touched);
        if (violations > 0)
          throw new Error(
            `Schema step ${step.revision} left ${violations} foreign key violation(s)`,
          );
        const changed = connection
          .prepare('UPDATE alembic_version SET version_num = ? WHERE version_num = ?')
          .run(step.revision, step.previous_revision).changes;
        if (changed !== 1)
          throw new Error(`Schema step ${step.revision} changed ${changed} revision rows`);
        if (
          index === pending.length - 1 &&
          options.expected_statements !== null &&
          !sameStatements(
            connection.prepare(SEED_STATEMENT_QUERY).pluck().all() as string[],
            options.expected_statements,
          )
        )
          throw new Error(
            `The schema after ${step.revision} differs from this release's seed, so the step was undone`,
          );
        connection.exec('COMMIT');
      } catch (error) {
        if (connection.inTransaction) connection.exec('ROLLBACK');
        throw error;
      }
      if (scopedIntegrityProblems(connection, integrity_scope).length > 0)
        throw new Error(`SQLite integrity check failed after schema step ${step.revision}`);
      applied.push(step.revision);
    }
    return { from_revision, to_revision: applied.at(-1)!, applied };
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
        result = timed_task('db.write', () =>
          scope(writer, true, work, options.auxiliary, options.safeIntegers),
        );
        for (const step of commitSteps)
          timed_task('db.commit_step', () => scope(writer, true, step), step.name || undefined);
        const after = writer.prepare('SELECT total_changes() AS count').get() as { count: number };
        changed = after.count !== before.count;
        timed_task('db.commit', () => writer.exec('COMMIT'));
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
        return timed_task('db.read', () => reader.read(work));
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
    throw new Error(refusalMessage);
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
      safeIntegers: options.safeIntegers ?? true,
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
