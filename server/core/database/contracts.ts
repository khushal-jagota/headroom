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

/** Build-time DDL only. Generated identities and domain rows never enter this artifact. */
export interface ApplicationSchemaSeed {
  readonly statements: readonly string[];
  readonly alembicRevision: string;
}

/** Synchronous domain bootstrap, after DDL and its frontier in the same write scope. */
export type FreshDatabaseBootstrap = (tx: TransactionSql) => void;

/** File maintenance stays in core. These signatures do not expose a native driver. */
export interface DatabaseFileOperations {
  /** Existing source; owns its private read connection through online-copy settlement. */
  onlineBackup(sourcePath: string, destinationPath: string): Promise<void>;
  /** Existing image; requires the first SQLite integrity-check result to be "ok". */
  verifyDatabaseIntegrity(path: string): void;
  /** Uses this owned capability, outside a transaction, with the main busy timeout. */
  checkpointDatabase(database: Database, mode: 'PASSIVE' | 'TRUNCATE'): CheckpointResult;
  /** New path only; one canonical write installs DDL, frontier, then required rows. */
  createDatabaseFromSeed(
    path: string,
    seed: ApplicationSchemaSeed,
    bootstrap: FreshDatabaseBootstrap,
  ): Database;
}

/** Dedicated domain storage uses the same writer engine without app projections. */
export interface AuxiliaryDatabaseOptions {
  schemaVersion: number;
  initialize(tx: TransactionSql): void;
  maxBytes: number;
  busyTimeoutMs?: number;
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
