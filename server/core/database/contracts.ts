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
