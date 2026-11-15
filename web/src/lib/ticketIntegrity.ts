import type { BrokenTicketProjection } from './ticketWorkspace/contracts';

/** A diagnostic does not promise valid lifecycle fields or a Worker manifest. */
export function isBrokenTicket(value: unknown): value is BrokenTicketProjection {
  return typeof value === 'object' && value !== null && 'integrity_error' in value;
}
