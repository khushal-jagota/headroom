import type { SchemaStep } from './contracts.ts';

export const owner_time_zone_step: SchemaStep = {
  revision: 'ts_0015_owner_time_zone',
  previous_revision: 'ts_0014_work_move_operations',
  apply(tx) {
    tx.run('ALTER TABLE attention_history_metadata ADD COLUMN owner_time_zone TEXT');
  },
};
