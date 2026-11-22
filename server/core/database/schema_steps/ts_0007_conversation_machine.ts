/**
 * Step 7: every conversation records the machine that runs it, fixed at its start.
 *
 * Conversations that exist before this step ran on the server's own machine, so they take
 * the home machine. A Ticket's conversation follows its Ticket's machine instead.
 */
import type { SchemaStep } from './contracts.ts';
export const conversation_machine_step: SchemaStep = {
  revision: 'ts_0007_conversation_machine',
  previous_revision: 'ts_0006_machine_backend_state',
  apply(tx) {
    tx.run(
      "ALTER TABLE conversations ADD COLUMN machine_id TEXT NOT NULL DEFAULT '75a49e78-97de-4e28-8dc5-79646761c46f' REFERENCES machines(machine_id)",
    );
  },
};
