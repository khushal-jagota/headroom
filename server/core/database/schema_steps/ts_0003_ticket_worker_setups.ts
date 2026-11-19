/**
 * Step 3: a Ticket declares that its Worker is setting up.
 *
 * `ticket_worker_setups` holds at most one open setup for each Ticket: the conversation
 * that is starting, the machine that does the setup, its current step, and when setup began.
 * The machine that does the setup opens the row and moves its step. The row is gone once
 * setup ends, so an open setup is exactly a present row.
 *
 * On another machine, setup runs inside that machine's `start` command. The trigger
 * `ticket_worker_setups_start_settled` closes the row whenever that command settles in any
 * way (finished, withdrawn or expired), so no settlement path can leave it open.
 *
 * The three navigation triggers mark the Ticket dirty, like `ticket_blocks`, so the
 * Ticket's navigation record carries the setup.
 */
import type { SchemaStep } from './contracts.ts';

export const ticket_worker_setups_step: SchemaStep = {
  revision: 'ts_0003_ticket_worker_setups',
  previous_revision: 'ts_0002_project_repository',
  apply(tx) {
    tx.run(
      "CREATE TABLE ticket_worker_setups (ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,conversation_id TEXT NOT NULL,machine_id TEXT NOT NULL,step TEXT NOT NULL CHECK(step IN ('preparing_workspace','starting_worker')),began_at INTEGER NOT NULL)",
    );
    tx.run(
      "CREATE TRIGGER ticket_worker_setups_start_settled AFTER UPDATE OF state ON executor_commands WHEN NEW.operation='start' AND NEW.state IN ('finished','withdrawn','expired') BEGIN DELETE FROM ticket_worker_setups WHERE conversation_id=NEW.conversation_id; END",
    );
    tx.run(
      "CREATE TRIGGER navigation_ticket_worker_setups_delete BEFORE DELETE ON ticket_worker_setups BEGIN INSERT INTO navigation_dirty(kind,id) SELECT 'ticket_worker_setups',OLD.ticket_id WHERE NOT EXISTS(SELECT 1 FROM navigation_dirty WHERE kind='ticket_worker_setups' AND id=OLD.ticket_id); END",
    );
    tx.run(
      "CREATE TRIGGER navigation_ticket_worker_setups_insert AFTER INSERT ON ticket_worker_setups BEGIN INSERT INTO navigation_dirty(kind,id) SELECT 'ticket_worker_setups',NEW.ticket_id WHERE NOT EXISTS(SELECT 1 FROM navigation_dirty WHERE kind='ticket_worker_setups' AND id=NEW.ticket_id); END",
    );
    tx.run(
      "CREATE TRIGGER navigation_ticket_worker_setups_update BEFORE UPDATE ON ticket_worker_setups BEGIN INSERT INTO navigation_dirty(kind,id) SELECT 'ticket_worker_setups',OLD.ticket_id WHERE NOT EXISTS(SELECT 1 FROM navigation_dirty WHERE kind='ticket_worker_setups' AND id=OLD.ticket_id);INSERT INTO navigation_dirty(kind,id) SELECT 'ticket_worker_setups',NEW.ticket_id WHERE NOT EXISTS(SELECT 1 FROM navigation_dirty WHERE kind='ticket_worker_setups' AND id=NEW.ticket_id); END",
    );
  },
};
