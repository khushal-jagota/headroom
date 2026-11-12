import type { TransactionSql } from '../../../core/database/contracts.ts';
export function initialize_metrics_schema(tx: TransactionSql): void {
  for (const statement of [
    'CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS samples(seq INTEGER PRIMARY KEY AUTOINCREMENT,timestamp REAL NOT NULL,boot TEXT NOT NULL,monotonic REAL NOT NULL,payload TEXT NOT NULL,bytes INTEGER NOT NULL,UNIQUE(boot,monotonic))',
    'CREATE TABLE IF NOT EXISTS snapshots(seq INTEGER PRIMARY KEY,timestamp REAL NOT NULL,payload TEXT NOT NULL,bytes INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS aggregates(resolution TEXT NOT NULL,bucket INTEGER NOT NULL,boot TEXT NOT NULL,entity TEXT NOT NULL,payload BLOB NOT NULL,bytes INTEGER NOT NULL,PRIMARY KEY(resolution,bucket,boot,entity))',
    'CREATE INDEX IF NOT EXISTS aggregates_entity_time ON aggregates(resolution,entity,bucket)',
    'CREATE INDEX IF NOT EXISTS samples_time ON samples(timestamp)',
    'CREATE INDEX IF NOT EXISTS snapshots_time ON snapshots(timestamp)',
    'CREATE TABLE IF NOT EXISTS payload_totals(kind TEXT PRIMARY KEY,bytes INTEGER NOT NULL)',
    "INSERT OR IGNORE INTO payload_totals VALUES('metrics',0),('snapshots',0)",
  ])
    tx.run(statement);
  for (const [table, kind] of [
    ['samples', 'metrics'],
    ['aggregates', 'metrics'],
    ['snapshots', 'snapshots'],
  ]) {
    for (const [action, expression] of [
      ['INSERT', 'NEW.bytes'],
      ['DELETE', '-OLD.bytes'],
      ['UPDATE', 'NEW.bytes-OLD.bytes'],
    ])
      tx.run(
        `CREATE TRIGGER IF NOT EXISTS ${table}_${action}_bytes AFTER ${action} ON ${table} BEGIN UPDATE payload_totals SET bytes=bytes+(${expression}) WHERE kind='${kind}'; END`,
      );
  }
}
