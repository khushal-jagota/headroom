/** Work keeps its destination independently of the conversation that last ran it. */
import type { SchemaStep } from './contracts.ts';
export const work_moves_step: SchemaStep = {
  revision: 'ts_0009_work_moves',
  previous_revision: 'ts_0008_conversation_machine',
  apply(tx) {
    tx.run('ALTER TABLE agents ADD COLUMN machine_id TEXT REFERENCES machines(machine_id)');
    tx.run('ALTER TABLE tickets ADD COLUMN published_branch TEXT');
    tx.run('ALTER TABLE tickets ADD COLUMN published_revision TEXT');
  },
};
