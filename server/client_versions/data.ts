import type { Database, ReadSql } from '../core/database/contracts.ts';
import type { ClientIdentity } from './contracts.ts';

/** The update-status read reconciles these declarations without a screen wake per request. */
export function remember_client_version(
  database: Database,
  identity: ClientIdentity,
  now: number,
): void {
  database.write(
    (tx) => {
      tx.run(
        'INSERT INTO client_versions(kind,device_id,identity_json,last_seen_at) VALUES(?,?,?,?) ON CONFLICT(kind,device_id) DO UPDATE SET identity_json=excluded.identity_json,last_seen_at=excluded.last_seen_at',
        [identity.kind, identity.device_id, JSON.stringify(identity), now],
      );
    },
    { changeSignal: 'silent' },
  );
}

/** Retain the last declaration from every device, including one that is away. */
export function list_client_versions(sql: ReadSql): ClientIdentity[] {
  return sql
    .all<{ identity_json: string }>(
      'SELECT identity_json FROM client_versions ORDER BY kind,device_id',
    )
    .map((row) => JSON.parse(row.identity_json) as ClientIdentity);
}
