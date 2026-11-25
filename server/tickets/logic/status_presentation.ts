/** Shared human wording for the canonical Ticket status. */
export const WORKER_STATUS_LABEL = 'Agent owned';

// Older servers and protected decision receipts can still supply the former name.
export function isAgentOwnedStatus(status: string | null | undefined): boolean {
  return status === 'agent_owned' || status === 'agent';
}

export function ticketStatusLabel(status: string): string {
  if (isAgentOwnedStatus(status)) return WORKER_STATUS_LABEL;
  const text = String(status).replace(/_/g, ' ');
  return text ? text[0].toUpperCase() + text.slice(1) : '';
}
