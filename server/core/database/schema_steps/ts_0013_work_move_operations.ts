/** Move progress survives browser navigation and server interruption. */
import type { SchemaStep } from './contracts.ts';
export const work_move_operations_step: SchemaStep = {
  revision: 'ts_0013_work_move_operations',
  previous_revision: 'ts_0012_empty_attachments',
  apply(tx) {
    tx.run(
      'CREATE TABLE work_move_operations (operation_id TEXT PRIMARY KEY, started_at_ms INTEGER NOT NULL, finished_at_ms INTEGER, operation_json TEXT NOT NULL)',
    );
  },
};
