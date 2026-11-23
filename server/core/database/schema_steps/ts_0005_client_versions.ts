/** Last accepted version declaration per client installation. */
import type { SchemaStep } from './contracts.ts';
export const client_versions_step: SchemaStep = {
  revision: 'ts_0005_client_versions',
  previous_revision: 'ts_0004_drop_ideas',
  apply(tx) {
    tx.run(
      'CREATE TABLE client_versions (kind TEXT NOT NULL,device_id TEXT NOT NULL,identity_json TEXT NOT NULL,last_seen_at INTEGER NOT NULL,PRIMARY KEY(kind,device_id))',
    );
  },
};
