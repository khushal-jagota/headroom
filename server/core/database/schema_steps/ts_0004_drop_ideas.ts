/**
 * Step 4: Ideas are no longer a Panels object.
 *
 * The Chief keeps ideas as Markdown files in its home folder. Every live idea was
 * exported there before this release, so the step drops the table and its index.
 */
import type { SchemaStep } from './contracts.ts';

export const drop_ideas_step: SchemaStep = {
  revision: 'ts_0004_drop_ideas',
  previous_revision: 'ts_0003_ticket_worker_setups',
  apply(tx) {
    tx.run('DROP INDEX idx_ideas_project_id');
    tx.run('DROP TABLE ideas');
  },
};
