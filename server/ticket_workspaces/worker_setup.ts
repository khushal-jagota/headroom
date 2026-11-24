/**
 * A Ticket's Worker setup: the phase between a start and a running Worker, while a machine
 * prepares the Ticket workspace and launches the agent. The machine that does the work
 * declares it. An open setup is exactly a present `ticket_worker_setups` row.
 */
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';

export const WORKER_SETUP_STEPS = ['preparing_workspace', 'starting_worker'] as const;
export type WorkerSetupStep = (typeof WORKER_SETUP_STEPS)[number];
export interface WorkerSetup {
  readonly step: WorkerSetupStep;
  /** Unix seconds when this setup began. A later step keeps it. */
  readonly began_at: number;
  readonly machine_id: string;
}
/**
 * Restart Worker refuses a setup younger than this. Nothing bounds a clone, and another
 * machine that hangs or disappears mid-start never settles its start command, so an older
 * setup no longer proves a live Worker. A slow first clone took 2 min 19 s.
 */
export const WORKER_SETUP_RESTART_REFUSAL_SECONDS = 15 * 60;
const STEP_LABELS: Record<WorkerSetupStep, string> = {
  preparing_workspace: 'preparing workspace',
  starting_worker: 'starting Worker',
};

/** Open the setup for this start, or move it to a later step. A new start replaces an older one. */
export function declare_worker_setup(
  tx: TransactionSql,
  setup: {
    ticket_id: string;
    conversation_id: string;
    machine_id: string;
    step: WorkerSetupStep;
    now: number;
  },
): void {
  tx.run(
    'INSERT INTO ticket_worker_setups (ticket_id,conversation_id,machine_id,step,began_at) VALUES(?,?,?,?,?) ' +
      'ON CONFLICT(ticket_id) DO UPDATE SET step=excluded.step,machine_id=excluded.machine_id,' +
      'began_at=CASE WHEN conversation_id=excluded.conversation_id THEN began_at ELSE excluded.began_at END,' +
      'conversation_id=excluded.conversation_id',
    [setup.ticket_id, setup.conversation_id, setup.machine_id, setup.step, setup.now],
  );
}

/** The direct path set up inside the server process, so its setup never survives a restart.
 * Machine code reports its own setups, and its start command settling closes them. */
export function close_home_worker_setups(tx: TransactionSql): void {
  tx.run(
    "DELETE FROM ticket_worker_setups WHERE machine_id=? AND NOT EXISTS (SELECT 1 FROM executor_commands c WHERE c.conversation_id=ticket_worker_setups.conversation_id AND c.operation='start' AND c.state IN ('waiting','delivered'))",
    [HOME_MACHINE_ID],
  );
}

export function read_worker_setup(sql: ReadSql, ticket_id: string): WorkerSetup | null {
  return (
    sql.get<WorkerSetup>(
      'SELECT step,began_at,machine_id FROM ticket_worker_setups WHERE ticket_id=?',
      [ticket_id],
    ) ?? null
  );
}

export function worker_setup_step_label(step: WorkerSetupStep): string {
  return STEP_LABELS[step];
}

/** "Still setting up since 2026-11-19 02:44:47 UTC (preparing workspace)" */
export function still_setting_up(setup: WorkerSetup): string {
  const since = new Date(setup.began_at * 1000).toISOString().replace('T', ' ').slice(0, 19);
  return `Still setting up since ${since} UTC (${worker_setup_step_label(setup.step)})`;
}
