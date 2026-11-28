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
    'CREATE TABLE IF NOT EXISTS stream_closes(id INTEGER PRIMARY KEY AUTOINCREMENT,closed_at INTEGER NOT NULL,device_id TEXT,client_kind TEXT NOT NULL,route TEXT NOT NULL,opened_at INTEGER NOT NULL,age_ms INTEGER NOT NULL,bytes_written INTEGER NOT NULL,closed_by TEXT NOT NULL,last_write_ago_ms INTEGER NOT NULL,error_code TEXT)',
    'CREATE INDEX IF NOT EXISTS stream_closes_time ON stream_closes(closed_at)',
    'CREATE TABLE IF NOT EXISTS client_events(id INTEGER PRIMARY KEY AUTOINCREMENT,at INTEGER NOT NULL,received_at INTEGER NOT NULL,device_id TEXT NOT NULL,client_kind TEXT NOT NULL,kind TEXT NOT NULL,detail TEXT NOT NULL)',
    'CREATE INDEX IF NOT EXISTS client_events_time ON client_events(at)',
    'CREATE INDEX IF NOT EXISTS client_events_received ON client_events(received_at)',
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
  // A database made before WebSocket closes has no close columns.
  const columns = tx.all<{ name: string }>("SELECT name FROM pragma_table_info('stream_closes')");
  for (const column of ['close_code', 'last_sequence'])
    if (!columns.some((existing) => existing.name === column))
      tx.run(`ALTER TABLE stream_closes ADD COLUMN ${column} INTEGER`);
}
