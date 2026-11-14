/** The only SQL capability that a state transition or commit step receives. */
export type SqlValue = string | number | bigint | Buffer | null;
export type SqlParameters = readonly SqlValue[] | Record<string, SqlValue>;
export interface RunResult {
  changes: number;
  lastInsertRowid: number | bigint;
}
export interface ReadSql {
  get<Row>(sql: string, parameters?: SqlParameters): Row | undefined;
  all<Row>(sql: string, parameters?: SqlParameters): Row[];
}
export interface TransactionSql extends ReadSql {
  run(sql: string, parameters?: SqlParameters): RunResult;
  /** A synchronous nested write scope. A failure rolls back only this scope. */
  savepoint<T>(work: (tx: TransactionSql) => T): T;
}
export type CommitStep = (tx: TransactionSql) => void;
export interface WriteOptions {
  busyTimeoutMs?: number;
  changeSignal?: 'emit' | 'silent';
}
export interface WriteDoor {
  write<T>(work: (tx: TransactionSql) => T, options?: WriteOptions): T;
}
export interface ReadAccess {
  read<T>(work: (sql: ReadSql) => T): T;
  close(): void;
}
export interface Database extends WriteDoor, ReadAccess {}
/** Cleanup ownership can cross an opening failure without exposing a connection. */
export interface DatabaseCloseHandle {
  close(): void;
}
export interface DurableSidecarOpenFailure extends Error {
  database_cleanup: DatabaseCloseHandle;
  cleanup_error: unknown;
}

/** Build-time DDL only. Generated identities and domain rows never enter this artifact. */
export interface ApplicationSchemaSeed {
  readonly statements: readonly string[];
  readonly alembicRevision: string;
}

/** Synchronous domain bootstrap, after DDL and its frontier in the same write scope. */
export type FreshDatabaseBootstrap = (tx: TransactionSql) => void;

/** Dedicated domain storage uses the same writer engine without app projections. */
export interface AuxiliaryDatabaseOptions {
  schemaVersion: number;
  initialize(tx: TransactionSql): void;
  maxBytes: number;
  busyTimeoutMs?: number;
}
/** A frozen durable format. Existing version zero is incompatible, not a fresh store. */
export interface DurableSidecarDatabaseOptions {
  /** Use native numbers when the sidecar has no nanosecond metadata. */
  safeIntegers?: boolean;
  schemaVersion: number;
  initialize(tx: TransactionSql): void;
  foreignKeys: boolean;
  refusalMessage: string;
}
export interface AuxiliaryTransactionSql extends TransactionSql {
  incrementalVacuum(pages: number): void;
}
export interface StorageStats {
  pageSize: number;
  pageCount: number;
  freelistCount: number;
  maxPageCount: number;
  userVersion: number;
  autoVacuum: 'none' | 'full' | 'incremental';
  journalMode: string;
}
export interface CheckpointResult {
  busy: number;
  log: number;
  checkpointed: number;
}
export interface AuxiliaryDatabase extends ReadAccess {
  write<T>(work: (tx: AuxiliaryTransactionSql) => T, options?: WriteOptions): T;
  storageStats(): StorageStats;
  checkpoint(mode: 'PASSIVE' | 'TRUNCATE', options?: { busyTimeoutMs?: number }): CheckpointResult;
}
