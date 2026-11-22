import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { read_ticket } from '../tickets/data/index.ts';
import { PreviewError, type TicketEligibility } from './contracts.ts';
export const TABLE = 'ticket_application_previews';
export function eligibility(sql: ReadSql, ticket_id: string): TicketEligibility {
  let ticket;
  try {
    ticket = read_ticket(sql, ticket_id);
  } catch (cause) {
    if (!(cause instanceof PlannerError && cause.code === 'not_found'))
      console.error('preview Ticket could not be read', ticket_id, cause);
    throw new PreviewError('This Ticket no longer exists.', 410, { cause });
  }
  const row = sql.get<{ folder_path: string | null }>(
    'SELECT folder_path FROM projects WHERE id=?',
    [ticket.project_id],
  );
  return {
    ticket_id,
    project_folder: row?.folder_path || null,
    terminal: ticket.workflow.stages.find((s) => s.id === ticket.stage)!.is_terminal,
    machine_id: ticket.machine_id,
  };
}
