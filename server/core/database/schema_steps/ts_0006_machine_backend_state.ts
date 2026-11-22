/** Saved provider observations belong to their execution machine. */
import type { SchemaStep } from './contracts.ts';
export const machine_backend_state_step: SchemaStep = {
  revision: 'ts_0006_machine_backend_state',
  previous_revision: 'ts_0005_server_link',
  apply(tx) {
    tx.run(
      'CREATE TABLE machine_backend_state (machine_id TEXT NOT NULL REFERENCES machines(machine_id), backend_key TEXT NOT NULL, capabilities_json TEXT, usage_json TEXT, PRIMARY KEY(machine_id,backend_key))',
    );
  },
};
