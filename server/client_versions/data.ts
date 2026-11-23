import type { Database } from '../core/database/contracts.ts';
import type { ClientIdentity } from './contracts.ts';

/** No screen subscribes to diagnostic version rows. */
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
