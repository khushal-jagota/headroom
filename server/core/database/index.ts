import BetterSqlite3 from 'better-sqlite3';
import type {
  CommitStep, Database, ReadAccess, ReadSql, SqlParameters, TransactionSql,
} from './contracts.ts';
export type * from './contracts.ts';

const BUSY_TIMEOUT_MS = 5000;

function assertSynchronous<T>(result: T): T {
  if (result !== null && (typeof result === 'object' || typeof result === 'function')
      && typeof (result as { then?: unknown }).then === 'function') {
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
  if (!['SELECT', 'WITH', 'VALUES', 'INSERT', 'UPDATE', 'DELETE', 'REPLACE',
    'CREATE', 'ALTER', 'DROP', 'EXPLAIN', 'ANALYZE', 'REINDEX'].includes(firstWord(sql))) {
    throw new TypeError('transaction control and connection configuration belong to the write door');
  }
}

function invoke<Row>(statement: BetterSqlite3.Statement, method: 'get' | 'all' | 'run',
                     parameters?: SqlParameters): Row {
  const values = parameters ?? [];
  return (Array.isArray(values)
    ? statement[method](...values)
    : statement[method](values)) as Row;
}

function scope<T>(connection: BetterSqlite3.Database, writable: boolean,
                  work: (sql: TransactionSql) => T): T {
  let active = true;
  function prepare(sql: string): BetterSqlite3.Statement {
    if (!active) throw new TypeError('database SQL scope is no longer active');
    assertScopedStatement(sql);
    return connection.prepare(sql);
  }
  const reader: ReadSql = Object.freeze({
    get: <Row>(sql: string, parameters?: SqlParameters): Row | undefined =>
      invoke<Row | undefined>(prepare(sql), 'get', parameters),
    all: <Row>(sql: string, parameters?: SqlParameters): Row[] =>
      invoke<Row[]>(prepare(sql), 'all', parameters),
  });
  const capability = writable ? Object.freeze({
    ...reader,
    run: (sql: string, parameters?: SqlParameters) =>
      invoke<BetterSqlite3.RunResult>(prepare(sql), 'run', parameters),
  }) : reader;
  try {
    return assertSynchronous(work(capability as TransactionSql));
  } finally {
    active = false;
  }
}

/** A worker thread can open this capability without a writable connection. */
export function createReadAccess(path: string): ReadAccess {
  const connection = new BetterSqlite3(path, { readonly: true, fileMustExist: true, timeout: BUSY_TIMEOUT_MS });
  try {
    connection.pragma('query_only = ON');
    connection.pragma('foreign_keys = ON');
    connection.pragma('recursive_triggers = ON');
  } catch (error) {
    connection.close();
    throw error;
  }
  let reading = false;
  let closed = false;
  return Object.freeze({
    read<T>(work: (sql: ReadSql) => T): T {
      if (closed) throw new Error('database is closed');
      if (reading) throw new TypeError('nested read callbacks are not allowed; use the current read scope');
      reading = true;
      try {
        connection.exec('BEGIN');
        const result = scope(connection, false, work);
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
      if (closed) return;
      if (reading) throw new TypeError('cannot close an active database scope');
      connection.close();
      closed = true;
    },
  });
}

/** Opens an existing Python-created database. No schema creation or migration. */
export function createDatabase(path: string, options: {
  commitSteps?: readonly CommitStep[];
  onChange?: () => void;
} = {}): Database {
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
  const commitSteps = [...options.commitSteps ?? []];
  let writing = false;
  let closed = false;
  const checkOpen = () => {
    if (closed) throw new Error('database is closed');
  };
  return Object.freeze({
    write<T>(work: (tx: TransactionSql) => T, writeOptions?: { changeSignal?: 'emit' | 'silent' }): T {
      checkOpen();
      if (writing) throw new TypeError('nested write door calls are not allowed; use the current transaction');
      writing = true;
      let result: T;
      let changed = false;
      try {
        writer.exec('BEGIN IMMEDIATE');
        const before = writer.prepare('SELECT total_changes() AS count').get() as { count: number };
        result = scope(writer, true, work);
        for (const step of commitSteps) scope(writer, true, step);
        const after = writer.prepare('SELECT total_changes() AS count').get() as { count: number };
        changed = after.count !== before.count;
        writer.exec('COMMIT');
      } catch (error) {
        if (writer.inTransaction) writer.exec('ROLLBACK');
        throw error;
      } finally {
        writing = false;
      }
      if (changed && writeOptions?.changeSignal !== 'silent') {
        try { options.onChange?.(); } catch (error) {
          console.error('post-commit change signal failed', error);
        }
      }
      return result;
    },
    read<T>(work: (sql: ReadSql) => T): T {
      checkOpen();
      return reader.read(work);
    },
    close(): void {
      if (closed) return;
      if (writing) throw new TypeError('cannot close an active database scope');
      reader.close();
      writer.close();
      closed = true;
    },
  });
}
