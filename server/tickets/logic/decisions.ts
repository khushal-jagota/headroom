import type { Ticket } from '../contracts.ts';
export type Decision = Pick<
  Ticket,
  'field_values' | 'field_approvals' | 'pending_proposal' | 'stage' | 'ceiling' | 'ceiling_holder'
>;
export function from_ticket(ticket: Ticket): Decision {
  return {
    field_values: { ...ticket.field_values },
    field_approvals: { ...ticket.field_approvals },
    pending_proposal: ticket.pending_proposal,
    stage: ticket.stage,
    ceiling: ticket.ceiling,
    ceiling_holder: ticket.ceiling_holder,
  };
}
