/** Human wording only. Ticket status values remain unchanged on the wire. */
export const WORKER_STATUS_LABEL = 'With the Worker';

export function ticketStatusLabel(status: string): string {
  if (status === 'agent') return WORKER_STATUS_LABEL;
  const text = String(status).replace(/_/g, ' ');
  return text ? text[0].toUpperCase() + text.slice(1) : '';
}
